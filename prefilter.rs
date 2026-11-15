//! Accelerate large persistence scans by verifying only possible match starts.

use fancy_regex::{Assertion, Expr, Regex, RegexBuilder};

// A failed acceleration attempt falls back to the original matcher. Bound the
// extra backtracking before fallback to at most 2 Mi steps per match search.
const VERIFY_STEPS: usize = 262_144;
const MAX_REJECTED_CANDIDATES: usize = 8;

pub(super) struct Prefilter {
    candidates: regex::Regex,
    exact: Regex,
}

impl Prefilter {
    pub(super) fn new(pattern: &str) -> Option<Self> {
        let mut expression = Expr::parse_tree(pattern).ok()?.expr;
        if !relax(&mut expression) {
            return None;
        }
        let mut permissive = String::new();
        expression.to_str(&mut permissive, 0);
        let candidates = regex::Regex::new(&permissive).ok()?;
        let exact = RegexBuilder::new(&format!(r"\G(?:{pattern})"))
            .backtrack_limit(VERIFY_STEPS)
            .build()
            .ok()?;
        Some(Self { candidates, exact })
    }

    pub(super) fn find_all(&self, code: &str) -> Option<Vec<String>> {
        let mut evidence = Vec::new();
        let mut pos = 0;
        let mut rejected = 0;
        while let Some(candidate) = self.candidates.find_at(code, pos) {
            let start = candidate.start();
            let result = self.exact.find_from_pos(code, start).ok()?;
            if let Some(found) = result {
                // The exact regex sees the original string, including anchors
                // and lookbehind. Only exact, nonempty evidence is returned.
                if found.start() != start || found.end() == start {
                    return None;
                }
                evidence.push(found.as_str().to_owned());
                pos = found.end();
                rejected = 0;
            } else {
                rejected += 1;
                if rejected >= MAX_REJECTED_CANDIDATES {
                    return None;
                }
                // Drop just the first character, not the entire relaxed match:
                // an exact match may start inside a rejected candidate.
                pos = start + code[start..].chars().next()?.len_utf8();
            }
        }
        Some(evidence)
    }
}

// Removing an assertion in a concatenation, alternative, group or repeat can
// only add match starts. Backreferences, atomic choices and conditionals are
// deliberately left to the original matcher.
fn relax(expression: &mut Expr) -> bool {
    match expression {
        Expr::LookAround(_, _) | Expr::Empty => {
            // Keep an empty atom so a surrounding quantifier still applies to
            // it, including when the previous token could accept a quantifier.
            *expression = Expr::Delegate {
                inner: "(?:)".to_owned(),
                size: 0,
                casei: false,
            };
        }
        Expr::Concat(children) | Expr::Alt(children) => return children.iter_mut().all(relax),
        Expr::Group(child) | Expr::Repeat { child, .. } => return relax(child),
        Expr::Assertion(Assertion::WordBoundary | Assertion::NotWordBoundary) => {
            let inner = if matches!(expression, Expr::Assertion(Assertion::WordBoundary)) {
                r"\b"
            } else {
                r"\B"
            };
            *expression = Expr::Delegate {
                inner: inner.to_owned(),
                size: 0,
                casei: false,
            };
        }
        Expr::Literal { .. }
        | Expr::Any { .. }
        | Expr::Delegate { .. }
        | Expr::Assertion(
            Assertion::StartText
            | Assertion::EndText
            | Assertion::StartLine { .. }
            | Assertion::EndLine { .. },
        ) => (),
        _ => return false,
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relaxed_candidates_preserve_exact_overlapping_starts_and_unicode() {
        let pattern = r"a(?!a)ab|ab|\bβ(?=!)|(?m:^)(?<!X)ok";
        let code = "aab β!\nXok\nok";
        let baseline = Regex::new(pattern).unwrap();
        let expected: Vec<_> = baseline
            .find_iter(code)
            .map(|m| m.unwrap().as_str().to_owned())
            .collect();
        assert_eq!(expected, ["ab", "β", "ok"]);
        assert_eq!(Prefilter::new(pattern).unwrap().find_all(code).unwrap(), expected);
    }

    #[test]
    fn unsupported_or_repeated_false_candidates_use_original_matcher() {
        assert!(Prefilter::new(r"(a)\1").is_none());
        assert!(Prefilter::new(r"(?>a|ab)c").is_none());
        let prefilter = Prefilter::new(r"a(?!b)").unwrap();
        assert!(prefilter.find_all(&"ab ".repeat(MAX_REJECTED_CANDIDATES)).is_none());
    }

    #[test]
    fn removed_quantified_assertion_does_not_bind_to_the_previous_token() {
        let mut expr = Expr::parse_tree(r"b(?=a){2}a").unwrap().expr;
        assert!(relax(&mut expr));
        let mut pattern = String::new();
        expr.to_str(&mut pattern, 0);
        assert!(regex::Regex::new(&pattern).unwrap().is_match("ba"));
    }
}
