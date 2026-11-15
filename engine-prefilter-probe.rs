use std::time::Instant;

use asc_capability_code_scan::{Language, load_rules};
use fancy_regex::{Regex, RegexBuilder};

const BACKTRACK_LIMIT: usize = 32_000_000;

fn compile(pattern: &str) -> Regex {
    let mut builder = RegexBuilder::new(pattern);
    builder.backtrack_limit(BACKTRACK_LIMIT);
    builder.build().expect("valid probe regex")
}

fn ranges(regex: &Regex, code: &str) -> fancy_regex::Result<Vec<(usize, usize)>> {
    regex
        .find_iter(code)
        .map(|found| found.map(|m| (m.start(), m.end())))
        .collect()
}

fn prefiltered_ranges(
    prefilter: &Regex,
    exact: &Regex,
    code: &str,
) -> fancy_regex::Result<Vec<(usize, usize)>> {
    let mut matches = Vec::new();
    let mut pos = 0;
    while let Some(candidate) = prefilter.find_from_pos(code, pos).inspect_err(|error| {
        eprintln!("prefilter search from byte {pos} failed: {error:?}");
    })? {
        let start = candidate.start();
        if let Some(found) = exact.find_from_pos(code, start).inspect_err(|error| {
            eprintln!("exact verification at byte {start} failed: {error:?}");
        })? {
            assert_eq!(found.start(), start, "anchored retry changed start position");
            assert!(found.end() > start, "rule never matches an empty substring");
            matches.push((start, found.end()));
            pos = found.end();
        } else if let Some(first) = code[start..].chars().next() {
            pos = start + first.len_utf8();
        } else {
            break;
        }
    }
    Ok(matches)
}

#[test]
fn measure_persistence_prefilter_against_current_engine() {
    let rule = load_rules(Language::Bash)
        .unwrap()
        .into_iter()
        .find(|rule| rule.rule_id == "shell-persistence")
        .unwrap();
    let original = compile(&rule.regex);
    let exact = compile(&format!(r"\G(?:{})", rule.regex));
    let widened = rule.regex
        .replace(r"(?!#)", "")
        .replace(r#"(?!["'])"#, "")
        .replace(r"(?<![=])", "")
        .replace(r"(?!\s+-[lre]\b)", "");
    assert!(!widened.contains("(?!") && !widened.contains("(?<!"));
    let prefilter = compile(&widened);
    let samples = [
        "echo αβ\nsystemctl enable fixture.service\n",
        "# systemctl enable fixture.service\n\tsystemctl enable test.service",
        "prefix=(systemctl enable no.service)\n(systemctl enable yes.service)",
        "foo='multi\nline' systemctl enable fixture.service\n",
        "echo 'literal; systemctl enable no.service'\ncrontab -l\ncrontab sample",
        "echo hello >> ~/.bashrc\ntee /etc/profile <<'E'\ntext\nE",
        "if ! sudo -u nobody env ABC=1 /usr/bin/systemctl --user enable demo; then :; fi",
        "echo $(systemctl enable sample) | tee /etc/profile\n",
    ];
    for sample in samples {
        let expected = ranges(&original, sample).unwrap();
        let actual = prefiltered_ranges(&prefilter, &exact, sample).unwrap();
        assert_eq!(actual, expected, "different evidence spans for {sample:?}");
    }
    let noise = concat!(
        "# setup step\nset -euo pipefail\n",
        "mkdir -p \"$HOME/work\" || true\n",
        "echo \"preparing\" >> \"$HOME/log.txt\"\n",
        "for f in *.txt; do\n",
        "    [ -f \"$f\" ] && cp \"$f\" \"$HOME/work/\" || echo \"skip $f\"\n",
        "done\n",
    );
    let mut code = noise.repeat(7_000);
    code.push_str("systemctl enable fixture.service\n");

    let boundaries = [r"|\btee\b", r"|(?m:", r"|\bcrontab\b"]
        .map(|marker| rule.regex.find(marker).unwrap());
    let ends = [boundaries[0], boundaries[1], boundaries[2], rule.regex.len()];
    let starts = [0, boundaries[0] + 1, boundaries[1] + 1, boundaries[2] + 1];
    for ((start, end), name) in starts.into_iter().zip(ends)
        .zip(["redirect", "tee", "systemctl", "crontab"])
    {
        let alternative = compile(&rule.regex[start..end]);
        let started = Instant::now();
        let observed = ranges(&alternative, &code);
        eprintln!("{name}, {:?}: {observed:?}", started.elapsed());
    }
    let started = Instant::now();
    let actual = prefiltered_ranges(&prefilter, &exact, &code);
    eprintln!("prefilter, {} bytes, {:?}: {actual:?}", code.len(), started.elapsed());
    let matches = actual.unwrap();
    assert_eq!(matches.len(), 1);
    let (start, end) = matches[0];
    assert_eq!(&code[start..end], "systemctl enable");
}
