use std::time::Instant;

use super::*;
use crate::rules::load_rules;

#[test]
fn profile_each_shipped_bash_rule_at_the_frame_limit() {
    let noise = concat!(
        "# setup step\nset -euo pipefail\n",
        "mkdir -p \"$HOME/work\" || true\n",
        "echo \"preparing\" >> \"$HOME/log.txt\"\n",
        "for f in *.txt; do\n",
        "    [ -f \"$f\" ] && cp \"$f\" \"$HOME/work/\" || echo \"skip $f\"\n",
        "done\n",
    );
    let encoded_block = serde_json::to_string(noise).unwrap().len() - 2;
    let mut code = noise.repeat((4 * 1024 * 1024 - 2048) / encoded_block);
    code.push_str("systemctl enable fixture.service\n");
    for rule in load_rules(Language::Bash).unwrap() {
        let started = Instant::now();
        let result = run_regex_rules(&code, std::slice::from_ref(&rule), Language::Bash);
        let status = match result {
            Ok(results) => format!("{} findings", results.len()),
            Err(error) => format!("error: {error}"),
        };
        eprintln!(
            "rule_profile {} targets={} {:?} {status}",
            rule.rule_id,
            rule.target_regexes.is_some_and(|targets| !targets.is_empty()),
            started.elapsed(),
        );
    }
}
