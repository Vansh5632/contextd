//! Making sure a briefing fits.
//!
//! A snapshot is pasted into someone's context window alongside their actual
//! code. If it is too big, the agent either truncates it at an arbitrary point
//! — losing whatever happened to be last — or the request fails. Neither is
//! acceptable, so the broker decides what to cut, in relevance order, rather
//! than letting the transport decide by accident.
//!
//! Token counting here is an estimate, not a tokeniser. Pulling in a real BPE
//! implementation would mean picking one model's vocabulary and being wrong for
//! every other client, for an accuracy we do not need: the budget exists to
//! prevent a blow-out, and being a little conservative costs nothing.

/// Bytes per token, averaged over the kind of text contextd produces: file
/// paths, shell commands, and short English sentences. Deliberately on the
/// pessimistic side, since paths tokenise worse than prose.
const CHARS_PER_TOKEN: usize = 3;

/// Default ceiling for one snapshot.
///
/// Small on purpose. The briefing is context *around* the user's real question,
/// not a replacement for it, and an agent that spends 20k tokens learning what
/// you were doing has less room to help you do it.
pub const DEFAULT_TOKEN_BUDGET: usize = 1_500;

/// Rough token count for a piece of text.
pub fn estimate_tokens(text: &str) -> usize {
    text.len().div_ceil(CHARS_PER_TOKEN)
}

/// Tracks how much room is left while a snapshot is assembled.
#[derive(Debug, Clone)]
pub struct TokenBudget {
    remaining: usize,
    spent: usize,
    /// Set once something has been left out, so the snapshot can say so.
    exhausted: bool,
}

impl TokenBudget {
    pub fn new(total: usize) -> Self {
        Self {
            remaining: total,
            spent: 0,
            exhausted: false,
        }
    }

    /// Spend budget on a piece of text, if it fits.
    ///
    /// Returns `false` when it does not, and remembers that something was
    /// dropped. Note that a single oversized item does not close the budget:
    /// a shorter, lower-ranked item after it may still fit, which is better
    /// than truncating the briefing at the first thing too large.
    pub fn try_spend(&mut self, text: &str) -> bool {
        let cost = estimate_tokens(text);
        if cost > self.remaining {
            self.exhausted = true;
            return false;
        }
        self.remaining -= cost;
        self.spent += cost;
        true
    }

    pub fn remaining(&self) -> usize {
        self.remaining
    }

    pub fn spent(&self) -> usize {
        self.spent
    }

    /// Whether anything had to be left out.
    pub fn truncated(&self) -> bool {
        self.exhausted
    }
}

/// The ellipsis appended to shortened text, in bytes. `…` is three in UTF-8,
/// and it counts against the allowance like everything else.
const ELLIPSIS_BYTES: usize = '…'.len_utf8();

/// Shorten text to fit a token allowance, on a word boundary where possible.
///
/// The result is guaranteed to fit: the marker is budgeted for, not added on
/// top. An allowance too small to hold even the marker yields nothing.
pub fn fit_to_tokens(text: &str, tokens: usize) -> String {
    let max_bytes = tokens.saturating_mul(CHARS_PER_TOKEN);
    if text.len() <= max_bytes {
        return text.to_string();
    }
    if max_bytes < ELLIPSIS_BYTES {
        return String::new();
    }

    // Respect char boundaries: paths and commands contain UTF-8 in the wild.
    let mut end = max_bytes - ELLIPSIS_BYTES;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }

    let slice = &text[..end];
    // Prefer a word boundary, but not when it would throw away most of what
    // fits — a long unbroken path has no whitespace, and "…" helps nobody.
    let cut = match slice.rfind(char::is_whitespace) {
        Some(boundary) if boundary * 2 >= end => boundary,
        _ => end,
    };

    format!("{}…", slice[..cut].trim_end())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_string_costs_nothing() {
        assert_eq!(estimate_tokens(""), 0);
    }

    #[test]
    fn estimating_never_rounds_down_to_zero_for_real_text() {
        // Rounding a short line to zero would let unlimited items through.
        assert!(estimate_tokens("x") >= 1);
        assert!(estimate_tokens("ab") >= 1);
    }

    #[test]
    fn spending_reduces_what_is_left() {
        let mut budget = TokenBudget::new(100);
        assert!(budget.try_spend(&"x".repeat(30)));

        assert_eq!(budget.spent(), 10);
        assert_eq!(budget.remaining(), 90);
        assert!(!budget.truncated());
    }

    #[test]
    fn an_item_that_does_not_fit_is_refused_and_recorded() {
        let mut budget = TokenBudget::new(5);
        assert!(!budget.try_spend(&"x".repeat(300)));

        assert_eq!(budget.remaining(), 5, "a refused item must cost nothing");
        assert!(budget.truncated());
    }

    #[test]
    fn one_oversized_item_does_not_close_the_budget() {
        // Otherwise a single verbose payload would silently drop everything
        // ranked below it, however short and however relevant.
        let mut budget = TokenBudget::new(20);
        assert!(!budget.try_spend(&"x".repeat(300)));
        assert!(budget.try_spend("still fits"));
        assert!(budget.spent() > 0);
    }

    #[test]
    fn an_exact_fit_is_allowed() {
        let mut budget = TokenBudget::new(10);
        assert!(budget.try_spend(&"x".repeat(30)));
        assert_eq!(budget.remaining(), 0);
        assert!(!budget.truncated());
    }

    #[test]
    fn text_within_the_allowance_is_untouched() {
        assert_eq!(fit_to_tokens("short", 100), "short");
    }

    #[test]
    fn long_text_is_shortened_and_marked() {
        let long = "the quick brown fox jumps over the lazy dog and keeps running";
        let fitted = fit_to_tokens(long, 5);

        assert!(fitted.ends_with('…'));
        assert!(estimate_tokens(&fitted) <= 5);
        assert!(long.starts_with(fitted.trim_end_matches('…').trim_end()));
    }

    #[test]
    fn shortening_prefers_a_word_boundary() {
        let fitted = fit_to_tokens("alpha beta gamma delta", 5);
        assert_eq!(fitted, "alpha beta…");
    }

    #[test]
    fn the_marker_is_budgeted_for_rather_than_added_on_top() {
        for tokens in 1..40 {
            let fitted = fit_to_tokens("alpha beta gamma delta epsilon zeta", tokens);
            assert!(
                estimate_tokens(&fitted) <= tokens,
                "{tokens} tokens produced {fitted:?}"
            );
        }
    }

    #[test]
    fn shortening_falls_back_to_a_hard_cut_rather_than_returning_almost_nothing() {
        // A long unbroken token — a path, a hash, a base64 blob — has no
        // whitespace to cut on, and returning "…" would be useless.
        let path = "/home/user/project/target/debug/build/some-crate-abcdef123456/out";
        let fitted = fit_to_tokens(path, 8);

        assert!(fitted.len() > 8, "expected a real prefix, got {fitted:?}");
        assert!(estimate_tokens(&fitted) <= 8);
    }

    #[test]
    fn shortening_does_not_split_a_multibyte_character() {
        let text = "café ☕ résumé naïve façade jalapeño piñata";
        for tokens in 1..12 {
            let fitted = fit_to_tokens(text, tokens);
            assert!(fitted.is_char_boundary(fitted.len()));
            assert!(estimate_tokens(&fitted) <= tokens);
        }
    }

    #[test]
    fn an_allowance_of_nothing_yields_nothing() {
        assert_eq!(fit_to_tokens("anything at all", 0), "");
    }
}
