//! Tier 2: what tends to go with what.
//!
//! Tiers 0 and 1 answer questions about *events*. This tier answers questions
//! about *things* — the file you keep coming back to, the command you always
//! run after touching it, the error that only ever appears in one module.
//!
//! The model is deliberately simple. Every event contributes a handful of
//! entities, and entities seen close together in time get an edge between them.
//! Run that over a week of work and the heavy edges are, empirically, the
//! structure of the project you are working on. No model is involved.
//!
//! The graph lives in RAM for querying and is mirrored to SQLite so a restart
//! does not start from nothing.

use std::collections::HashMap;

use contextd_core::event::{EventSource, ProcessedEvent};
use petgraph::graph::{NodeIndex, UnGraph};
use petgraph::visit::EdgeRef;
use serde::{Deserialize, Serialize};

/// How far apart two events can be and still count as related.
///
/// Five minutes is long enough to cover "edit, build, read the error, edit
/// again" and short enough that this morning's work does not get wired to this
/// afternoon's.
const CO_OCCURRENCE_WINDOW_MS: u64 = 5 * 60 * 1_000;

/// How many recent entities an incoming event is linked against.
///
/// Without a bound, a long session degenerates towards a complete graph, where
/// everything is related to everything and therefore nothing is.
const RECENT_ENTITY_WINDOW: usize = 8;

/// What a node in the graph represents.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    File,
    Command,
    Repo,
    Error,
    Intent,
}

impl EntityKind {
    pub fn as_str(self) -> &'static str {
        match self {
            EntityKind::File => "file",
            EntityKind::Command => "command",
            EntityKind::Repo => "repo",
            EntityKind::Error => "error",
            EntityKind::Intent => "intent",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "file" => EntityKind::File,
            "command" => EntityKind::Command,
            "repo" => EntityKind::Repo,
            "error" => EntityKind::Error,
            "intent" => EntityKind::Intent,
            _ => return None,
        })
    }
}

/// One thing worth remembering the existence of.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Entity {
    pub kind: EntityKind,
    pub name: String,
}

impl Entity {
    pub fn new(kind: EntityKind, name: impl Into<String>) -> Self {
        Self {
            kind,
            name: name.into(),
        }
    }

    /// Stable identity used as the persistence key.
    pub fn key(&self) -> String {
        format!("{}:{}", self.kind.as_str(), self.name)
    }
}

/// An entity and how strongly it relates to whatever was asked about.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Related {
    pub entity: Entity,
    pub weight: f32,
}

/// The whole graph, detached from the lock that guards it.
#[derive(Debug, Clone, Default)]
pub struct GraphSnapshot {
    pub entities: Vec<Entity>,
    pub edges: Vec<(Entity, Entity, f32)>,
}

impl GraphSnapshot {
    pub fn is_empty(&self) -> bool {
        self.entities.is_empty()
    }
}

/// Tier 2 itself.
pub struct KnowledgeGraph {
    graph: UnGraph<Entity, f32>,
    index: HashMap<Entity, NodeIndex>,
    /// Entities from recent events, newest last, used to form edges.
    recent: Vec<(Entity, u64)>,
}

impl Default for KnowledgeGraph {
    fn default() -> Self {
        Self::new()
    }
}

impl KnowledgeGraph {
    pub fn new() -> Self {
        Self {
            graph: UnGraph::new_undirected(),
            index: HashMap::new(),
            recent: Vec::new(),
        }
    }

    pub fn node_count(&self) -> usize {
        self.graph.node_count()
    }

    pub fn edge_count(&self) -> usize {
        self.graph.edge_count()
    }

    /// Fold one event into the graph.
    pub fn observe(&mut self, event: &ProcessedEvent) {
        let entities = entities_of(event);
        if entities.is_empty() {
            return;
        }

        let timestamp_ms = event.raw.timestamp_ms;
        self.recent.retain(|(_, seen_ms)| {
            timestamp_ms.saturating_sub(*seen_ms) <= CO_OCCURRENCE_WINDOW_MS
        });

        for entity in &entities {
            let node = self.node_for(entity);

            // Link against what came just before, not against the whole
            // session, so weight means "these two keep appearing together".
            let start = self.recent.len().saturating_sub(RECENT_ENTITY_WINDOW);
            let neighbours: Vec<Entity> = self.recent[start..]
                .iter()
                .map(|(entity, _)| entity.clone())
                .collect();

            for neighbour in neighbours {
                if neighbour == *entity {
                    continue;
                }
                let other = self.node_for(&neighbour);
                self.reinforce(node, other);
            }
        }

        for entity in entities {
            self.recent.push((entity, timestamp_ms));
        }
    }

    /// Entities most often seen alongside this one, strongest first.
    pub fn neighbours(&self, entity: &Entity, limit: usize) -> Vec<Related> {
        let Some(&node) = self.index.get(entity) else {
            return Vec::new();
        };

        let mut related: Vec<Related> = self
            .graph
            .edges(node)
            .map(|edge| {
                let other = if edge.source() == node {
                    edge.target()
                } else {
                    edge.source()
                };
                Related {
                    entity: self.graph[other].clone(),
                    weight: *edge.weight(),
                }
            })
            .collect();

        related.sort_by(|a, b| {
            b.weight
                .partial_cmp(&a.weight)
                .unwrap_or(std::cmp::Ordering::Equal)
                // Ties broken by name so output is reproducible in tests and logs.
                .then_with(|| a.entity.name.cmp(&b.entity.name))
        });
        related.truncate(limit);
        related
    }

    /// Every node, for persistence.
    pub fn entities(&self) -> Vec<Entity> {
        self.graph.node_weights().cloned().collect()
    }

    /// Every edge as `(from, to, weight)`, for persistence.
    pub fn edges(&self) -> Vec<(Entity, Entity, f32)> {
        self.graph
            .edge_references()
            .map(|edge| {
                (
                    self.graph[edge.source()].clone(),
                    self.graph[edge.target()].clone(),
                    *edge.weight(),
                )
            })
            .collect()
    }

    /// A detached copy of the whole graph.
    ///
    /// Persistence goes through this rather than borrowing the graph, so the
    /// lock is released before the SQLite transaction starts. Otherwise a flush
    /// would block ingest for the duration of a disk write.
    pub fn snapshot(&self) -> GraphSnapshot {
        GraphSnapshot {
            entities: self.entities(),
            edges: self.edges(),
        }
    }

    /// Restore an edge read back from storage.
    pub fn restore_edge(&mut self, from: Entity, to: Entity, weight: f32) {
        let a = self.node_for(&from);
        let b = self.node_for(&to);
        if let Some(edge) = self.graph.find_edge(a, b) {
            self.graph[edge] = weight;
        } else {
            self.graph.add_edge(a, b, weight);
        }
    }

    /// Restore a node with no edges yet.
    pub fn restore_entity(&mut self, entity: Entity) {
        self.node_for(&entity);
    }

    fn node_for(&mut self, entity: &Entity) -> NodeIndex {
        if let Some(&node) = self.index.get(entity) {
            return node;
        }
        let node = self.graph.add_node(entity.clone());
        self.index.insert(entity.clone(), node);
        node
    }

    fn reinforce(&mut self, a: NodeIndex, b: NodeIndex) {
        if let Some(edge) = self.graph.find_edge(a, b) {
            self.graph[edge] += 1.0;
        } else {
            self.graph.add_edge(a, b, 1.0);
        }
    }
}

/// Pull the entities worth remembering out of one event.
pub fn entities_of(event: &ProcessedEvent) -> Vec<Entity> {
    let payload = &event.raw.payload;
    // A blank value is not an entity. Left unchecked these became a single
    // unnamed node that everything linked to, which is worse than no node.
    let string = |key: &str| {
        payload
            .get(key)
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|value| !value.is_empty())
    };

    let mut entities = Vec::new();

    match event.raw.source {
        EventSource::Shell | EventSource::Proc => {
            if let Some(command) = string("command") {
                entities.push(Entity::new(EntityKind::Command, program_of(command)));
            }
        }
        EventSource::FileSystem | EventSource::Editor => {
            if let Some(path) = string("path") {
                entities.push(Entity::new(EntityKind::File, path));
            }
        }
        EventSource::Manifest => {
            if let Some(file) = string("file") {
                entities.push(Entity::new(EntityKind::File, file));
            }
        }
        EventSource::Git => {
            if let Some(repo) = string("repo") {
                entities.push(Entity::new(EntityKind::Repo, repo));
            }
        }
    }

    // An error is worth a node whatever produced it: "what else is going on
    // when this breaks" is the question this tier is best at. Reusing the
    // summariser's rule keeps the error node and the summary talking about the
    // same line.
    if let Some(error) = ["stderr", "output", "error", "message"]
        .iter()
        .filter_map(|field| string(field))
        .find_map(pipeline::content::extract_error)
    {
        entities.push(Entity::new(EntityKind::Error, error));
    }

    entities
}

/// Reduce a full command line to the program being run.
///
/// `cargo` and `cargo test --lib -p store` are the same node; otherwise every
/// distinct invocation becomes its own island and no edge ever gets heavy.
fn program_of(command: &str) -> String {
    let head = command.split_whitespace().next().unwrap_or(command);
    let name = head.rsplit('/').next().unwrap_or(head);

    // Keep the subcommand for multiplexers, where the bare program says nothing.
    if matches!(name, "cargo" | "git" | "npm" | "pnpm" | "yarn" | "go")
        && let Some(subcommand) = command
            .split_whitespace()
            .nth(1)
            .filter(|word| !word.starts_with('-'))
    {
        return format!("{name} {subcommand}");
    }

    name.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use contextd_core::event::RawEvent;
    use serde_json::json;

    fn event(source: EventSource, timestamp_ms: u64, payload: serde_json::Value) -> ProcessedEvent {
        ProcessedEvent::new(
            RawEvent {
                id: format!("evt-{timestamp_ms}"),
                timestamp_ms,
                source,
                payload,
            },
            0.6,
        )
    }

    fn file(path: &str, timestamp_ms: u64) -> ProcessedEvent {
        event(EventSource::FileSystem, timestamp_ms, json!({"path": path}))
    }

    fn shell(command: &str, timestamp_ms: u64) -> ProcessedEvent {
        event(
            EventSource::Shell,
            timestamp_ms,
            json!({"command": command}),
        )
    }

    fn git(payload: serde_json::Value, timestamp_ms: u64) -> ProcessedEvent {
        event(EventSource::Git, timestamp_ms, payload)
    }

    #[test]
    fn a_git_event_with_a_repo_becomes_a_repo_node() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&git(
            json!({
                "action": "commit",
                "hash": "abc123",
                "message": "feat: add graph",
                "repo": "/home/dev/contextd",
            }),
            1_000,
        ));

        // neighbours() is empty for a node with no edges *and* for a
        // missing node — node_count + entities() are what prove it exists.
        assert_eq!(graph.node_count(), 1);
        assert_eq!(
            graph.entities(),
            vec![Entity::new(EntityKind::Repo, "/home/dev/contextd")]
        );
    }

    #[test]
    fn a_git_event_without_a_repo_adds_no_repo_node() {
        // This is the payload shape hooks sent before this fix. A commit
        // subject that does not look like an error must not mint a node.
        let mut graph = KnowledgeGraph::new();
        graph.observe(&git(
            json!({
                "action": "commit",
                "hash": "abc123",
                "message": "feat: add graph",
            }),
            1_000,
        ));
        graph.observe(&git(
            json!({
                "action": "checkout",
                "message": "main",
                "from": "aaa",
                "to": "bbb",
            }),
            1_100,
        ));
        graph.observe(&git(
            json!({
                "action": "push",
                "remote": "origin",
                "message": "main",
            }),
            1_200,
        ));

        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn a_blank_repo_does_not_become_an_unnamed_node() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&git(json!({"action": "commit", "repo": ""}), 1_000));
        graph.observe(&git(json!({"action": "commit", "repo": "   "}), 1_100));

        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn a_commit_links_the_repo_to_the_file_just_edited() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&file("/home/dev/contextd/src/main.rs", 1_000));
        graph.observe(&git(
            json!({
                "action": "commit",
                "message": "feat: add graph",
                "repo": "/home/dev/contextd",
            }),
            2_000,
        ));

        let neighbours = graph.neighbours(&Entity::new(EntityKind::Repo, "/home/dev/contextd"), 5);
        assert_eq!(neighbours.len(), 1);
        assert_eq!(neighbours[0].entity.kind, EntityKind::File);
        assert_eq!(neighbours[0].entity.name, "/home/dev/contextd/src/main.rs");
    }

    #[test]
    fn a_command_run_after_a_file_edit_links_the_two() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&file("/repo/src/main.rs", 1_000));
        graph.observe(&shell("cargo test --lib", 2_000));

        let neighbours = graph.neighbours(&Entity::new(EntityKind::File, "/repo/src/main.rs"), 5);
        assert_eq!(neighbours.len(), 1);
        assert_eq!(neighbours[0].entity.name, "cargo test");
    }

    #[test]
    fn repetition_is_what_makes_an_edge_heavy() {
        let mut graph = KnowledgeGraph::new();
        for round in 0..3 {
            let base = round * 10_000;
            graph.observe(&file("/repo/src/main.rs", base));
            graph.observe(&shell("cargo test", base + 1_000));
        }
        graph.observe(&file("/repo/src/main.rs", 40_000));
        graph.observe(&shell("rustfmt", 41_000));

        let neighbours = graph.neighbours(&Entity::new(EntityKind::File, "/repo/src/main.rs"), 5);
        assert_eq!(
            neighbours[0].entity.name, "cargo test",
            "the habitual pairing should outrank the one-off"
        );
        assert!(neighbours[0].weight > neighbours[1].weight);
    }

    #[test]
    fn work_hours_apart_is_not_related_work() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&file("/repo/morning.rs", 0));
        graph.observe(&file(
            "/repo/afternoon.rs",
            CO_OCCURRENCE_WINDOW_MS + 60_000,
        ));

        assert_eq!(graph.node_count(), 2);
        assert_eq!(
            graph.edge_count(),
            0,
            "events outside the window must not be wired together"
        );
    }

    #[test]
    fn a_long_session_does_not_become_a_complete_graph() {
        // Every entity linking to every other would make weight meaningless.
        let mut graph = KnowledgeGraph::new();
        for index in 0..40u64 {
            graph.observe(&file(&format!("/repo/f{index}.rs"), index * 10));
        }

        let complete = graph.node_count() * (graph.node_count() - 1) / 2;
        assert!(
            graph.edge_count() < complete,
            "expected a sparse graph, got {} of {complete} possible edges",
            graph.edge_count()
        );
    }

    #[test]
    fn commands_collapse_to_the_program_being_run() {
        assert_eq!(program_of("/usr/bin/rustc --edition 2024"), "rustc");
        assert_eq!(program_of("cargo test --lib -p store"), "cargo test");
        assert_eq!(program_of("git commit -m 'x'"), "git commit");
        assert_eq!(program_of("cargo"), "cargo");
        assert_eq!(program_of("cargo --version"), "cargo");
        assert_eq!(program_of("ls"), "ls");
    }

    #[test]
    fn an_error_becomes_a_node_alongside_what_produced_it() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&file("/repo/src/auth.rs", 1_000));
        graph.observe(&event(
            EventSource::Shell,
            2_000,
            json!({
                "command": "cargo test",
                "stderr": "thread 'main' panicked at src/auth.rs:22:9",
            }),
        ));

        let neighbours = graph.neighbours(&Entity::new(EntityKind::File, "/repo/src/auth.rs"), 5);
        let kinds: Vec<EntityKind> = neighbours.iter().map(|r| r.entity.kind).collect();
        assert!(
            kinds.contains(&EntityKind::Error),
            "an error should be linked to the file being worked on"
        );
    }

    #[test]
    fn asking_about_an_unknown_entity_is_empty_not_a_panic() {
        let graph = KnowledgeGraph::new();
        assert!(
            graph
                .neighbours(&Entity::new(EntityKind::File, "/nope"), 5)
                .is_empty()
        );
    }

    #[test]
    fn an_event_with_nothing_extractable_adds_nothing() {
        let mut graph = KnowledgeGraph::new();
        graph.observe(&event(EventSource::Shell, 1_000, json!({})));

        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn a_blank_value_does_not_become_an_unnamed_node() {
        // These all collapsed to one empty-named node that everything linked
        // to, which polluted every "what relates to this" answer.
        let mut graph = KnowledgeGraph::new();
        graph.observe(&event(EventSource::Shell, 1_000, json!({"command": ""})));
        graph.observe(&event(EventSource::Shell, 1_100, json!({"command": "   "})));
        graph.observe(&event(EventSource::FileSystem, 1_200, json!({"path": ""})));

        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn entity_kinds_survive_a_round_trip_through_text() {
        for kind in [
            EntityKind::File,
            EntityKind::Command,
            EntityKind::Repo,
            EntityKind::Error,
            EntityKind::Intent,
        ] {
            assert_eq!(EntityKind::parse(kind.as_str()), Some(kind));
        }
        assert_eq!(EntityKind::parse("nonsense"), None);
    }

    #[test]
    fn restoring_edges_rebuilds_an_equivalent_graph() {
        let mut original = KnowledgeGraph::new();
        original.observe(&file("/repo/src/main.rs", 1_000));
        original.observe(&shell("cargo test", 2_000));

        let mut restored = KnowledgeGraph::new();
        for entity in original.entities() {
            restored.restore_entity(entity);
        }
        for (from, to, weight) in original.edges() {
            restored.restore_edge(from, to, weight);
        }

        assert_eq!(restored.node_count(), original.node_count());
        assert_eq!(restored.edge_count(), original.edge_count());
        assert_eq!(
            restored.neighbours(&Entity::new(EntityKind::File, "/repo/src/main.rs"), 5),
            original.neighbours(&Entity::new(EntityKind::File, "/repo/src/main.rs"), 5)
        );
    }

    #[test]
    fn restoring_the_same_edge_twice_sets_rather_than_accumulates() {
        let mut graph = KnowledgeGraph::new();
        let a = Entity::new(EntityKind::File, "a");
        let b = Entity::new(EntityKind::File, "b");

        graph.restore_edge(a.clone(), b.clone(), 3.0);
        graph.restore_edge(a.clone(), b, 3.0);

        assert_eq!(graph.edge_count(), 1);
        assert_eq!(graph.neighbours(&a, 5)[0].weight, 3.0);
    }
}
