//! Deciding what goes in the briefing when four tiers all have an opinion.
//!
//! Each tier answers a different question, and each is wrong on its own:
//!
//! - **Recency** alone gives you the last ten file saves, which is what a
//!   `tail -f` does and why raw activity logs are useless to an agent.
//! - **Importance** alone is a guess the scorer made in milliseconds.
//! - **Similarity** alone ignores time entirely, happily surfacing something
//!   from six weeks ago over what you did a minute ago.
//! - **Proximity** alone describes your habits, not your afternoon.
//!
//! So they are combined. The weights below are the part of contextd most worth
//! tuning against real use; they are deliberately in one place, named, and
//! summing to 1.0 so a change is easy to reason about.

use contextd_core::event::ProcessedEvent;

/// How long until an event counts for half of what it did when it happened.
///
/// Twenty minutes is roughly the length of one unit of focused work. Long
/// enough that starting a build does not immediately bury the edit that
/// prompted it, short enough that this morning loses to this minute.
const RECENCY_HALF_LIFE_MS: f32 = 20.0 * 60.0 * 1_000.0;

/// Graph edge weight treated as "as related as things get".
///
/// Weights grow without bound as a habit repeats, so they need a ceiling before
/// they can be mixed with the other signals, all of which are 0..1.
const PROXIMITY_SATURATION: f32 = 10.0;

/// What each signal is worth. These sum to 1.0, so a combined score is 0..1.
const W_RECENCY: f32 = 0.35;
const W_IMPORTANCE: f32 = 0.20;
const W_SIMILARITY: f32 = 0.35;
const W_PROXIMITY: f32 = 0.10;

/// The four signals behind one item's placement in a briefing.
///
/// Kept as a struct rather than collapsed straight to a number so the reason an
/// event was included stays inspectable — both in tests and when explaining a
/// surprising briefing.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Signals {
    /// How recently it happened, 0..1.
    pub recency: f32,
    /// What the scorer thought at the time, 0..1.
    pub importance: f32,
    /// How close in meaning to what was asked, 0..1. Zero when Ollama is down.
    pub similarity: f32,
    /// How strongly the knowledge graph links it to the present, 0..1.
    pub proximity: f32,
}

impl Signals {
    /// The single number used to order a briefing.
    pub fn combined(&self) -> f32 {
        (W_RECENCY * self.recency
            + W_IMPORTANCE * self.importance
            + W_SIMILARITY * self.similarity
            + W_PROXIMITY * self.proximity)
            .clamp(0.0, 1.0)
    }
}

/// Decay an event's weight by how long ago it happened.
pub fn recency(event_ms: u64, now_ms: u64) -> f32 {
    let age_ms = now_ms.saturating_sub(event_ms) as f32;
    0.5_f32.powf(age_ms / RECENCY_HALF_LIFE_MS).clamp(0.0, 1.0)
}

/// Turn a vector distance into a similarity.
///
/// sqlite-vec reports cosine *distance* in 0..2, where 0 is identical. Anything
/// beyond 1.0 is less alike than two random vectors, so it is worth nothing
/// rather than being worth a negative amount.
pub fn similarity(distance: f32) -> f32 {
    (1.0 - distance).clamp(0.0, 1.0)
}

/// Normalise an unbounded graph edge weight into 0..1.
pub fn proximity(edge_weight: f32) -> f32 {
    (edge_weight / PROXIMITY_SATURATION).clamp(0.0, 1.0)
}

/// Score one event against the current moment.
pub fn score(
    event: &ProcessedEvent,
    now_ms: u64,
    distance: Option<f32>,
    edge_weight: Option<f32>,
) -> Signals {
    Signals {
        recency: recency(event.raw.timestamp_ms, now_ms),
        importance: event.score.clamp(0.0, 1.0),
        similarity: distance.map(similarity).unwrap_or(0.0),
        proximity: edge_weight.map(proximity).unwrap_or(0.0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::{EventSource, RawEvent};
    use serde_json::json;

    fn event(timestamp_ms: u64, score: f32) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: "e".to_string(),
                timestamp_ms,
                source: EventSource::Shell,
                payload: json!({"command": "cargo test"}),
            },
            score,
        )
    }

    #[test]
    fn the_weights_sum_to_one_so_a_score_is_a_fraction() {
        assert!((W_RECENCY + W_IMPORTANCE + W_SIMILARITY + W_PROXIMITY - 1.0).abs() < f32::EPSILON);
    }

    #[test]
    fn everything_at_full_strength_scores_one() {
        let all = Signals {
            recency: 1.0,
            importance: 1.0,
            similarity: 1.0,
            proximity: 1.0,
        };
        assert!((all.combined() - 1.0).abs() < 1e-6);
    }

    #[test]
    fn nothing_at_all_scores_zero() {
        assert_eq!(Signals::default().combined(), 0.0);
    }

    #[test]
    fn something_that_just_happened_is_fully_recent() {
        assert_eq!(recency(1_000, 1_000), 1.0);
    }

    #[test]
    fn one_half_life_halves_the_weight() {
        let now = 10_000_000;
        let then = now - RECENCY_HALF_LIFE_MS as u64;
        assert!((recency(then, now) - 0.5).abs() < 1e-4);
    }

    #[test]
    fn recency_decays_but_never_goes_negative() {
        let now = 10_u64.pow(12);
        assert!(recency(0, now) >= 0.0);
        assert!(recency(0, now) < 1e-6);
    }

    #[test]
    fn an_event_from_the_future_is_not_worth_more_than_one() {
        // Clock skew between the git hook and the daemon is entirely possible.
        assert_eq!(recency(2_000, 1_000), 1.0);
    }

    #[test]
    fn an_identical_vector_is_fully_similar() {
        assert_eq!(similarity(0.0), 1.0);
    }

    #[test]
    fn a_vector_less_alike_than_chance_is_worth_nothing() {
        assert_eq!(similarity(1.0), 0.0);
        assert_eq!(similarity(1.9), 0.0);
    }

    #[test]
    fn graph_weight_saturates_rather_than_dominating() {
        // Edge weights grow without bound as a habit repeats. Left unnormalised
        // one strong pairing would outweigh every other signal combined.
        assert_eq!(proximity(PROXIMITY_SATURATION), 1.0);
        assert_eq!(proximity(PROXIMITY_SATURATION * 100.0), 1.0);
        assert_eq!(proximity(PROXIMITY_SATURATION / 2.0), 0.5);
    }

    #[test]
    fn a_recent_event_outranks_an_old_one_all_else_equal() {
        let now = 10_000_000;
        let fresh = score(&event(now, 0.5), now, None, None);
        let stale = score(&event(now - 3_600_000, 0.5), now, None, None);

        assert!(fresh.combined() > stale.combined());
    }

    #[test]
    fn a_strong_semantic_match_can_outrank_mere_recency() {
        // This is the whole reason for mixing signals: "the thing I was doing
        // when I last hit this error" should beat "the file I saved a moment
        // ago" when the question is about the error.
        let now = 10_000_000;
        let recent_noise = score(&event(now, 0.3), now, None, None);
        let old_but_apt = score(&event(now - 2 * 3_600_000, 0.9), now, Some(0.05), Some(8.0));

        assert!(old_but_apt.combined() > recent_noise.combined());
    }

    #[test]
    fn a_missing_signal_costs_nothing_rather_than_being_treated_as_zero_evidence() {
        // Ollama being down must degrade the ordering, not invert it.
        let now = 10_000_000;
        let with_ollama_down = score(&event(now, 0.9), now, None, None);
        assert!(with_ollama_down.combined() > 0.0);
        assert_eq!(with_ollama_down.similarity, 0.0);
    }

    #[test]
    fn importance_outside_the_expected_range_is_clamped() {
        let now = 1_000;
        assert_eq!(score(&event(now, 5.0), now, None, None).importance, 1.0);
        assert_eq!(score(&event(now, -1.0), now, None, None).importance, 0.0);
    }
}
