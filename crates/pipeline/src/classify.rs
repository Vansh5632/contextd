//! Deciding what kind of work an event represents, and how long it matters.
//!
//! Both classifiers are rules over the payload. That is a deliberate ceiling:
//! they are wrong sometimes, but they are wrong in the same way every time, they
//! cost nothing, and they work with no model installed. A local LLM can refine
//! these later — it must never be required to produce them.

use contextd_core::event::{EventSource, RawEvent};
use contextd_core::memory::{MemoryType, UseCase};

/// File extensions that mean somebody is writing software.
const CODE_EXTENSIONS: &[&str] = &[
    "rs", "ts", "tsx", "js", "jsx", "py", "go", "java", "kt", "rb", "php", "c", "h", "cpp", "hpp",
    "cs", "swift", "scala", "clj", "ex", "exs", "sql", "sh", "bash", "zsh", "vue", "svelte",
];

/// Extensions that are prose, not program.
const DOCUMENT_EXTENSIONS: &[&str] = &["md", "mdx", "rst", "txt", "adoc", "org", "pdf"];

/// Commands whose whole job is building, testing, or running code.
const CODING_COMMANDS: &[&str] = &[
    "cargo",
    "rustc",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "tsc",
    "go ",
    "gcc",
    "clang",
    "make",
    "cmake",
    "gradle",
    "mvn",
    "pytest",
    "jest",
    "vitest",
    "tox",
    "dotnet",
    "docker build",
    "bazel",
];

/// Commands that are looking things up rather than producing anything.
const RESEARCH_COMMANDS: &[&str] = &[
    "man ", "curl", "wget", "grep", "rg ", "ag ", "find ", "less ", "cat ", "tail ", "head ",
    "--help", "-h", "which ", "whereis",
];

/// Classify what kind of work this event belongs to.
pub fn classify_use_case(event: &RawEvent) -> UseCase {
    match event.source {
        // Version control is only ever used on code.
        EventSource::Git | EventSource::Manifest => UseCase::Coding,

        EventSource::FileSystem => match extension(path_of(event).unwrap_or_default()) {
            Some(extension) if CODE_EXTENSIONS.contains(&extension.as_str()) => UseCase::Coding,
            Some(extension) if DOCUMENT_EXTENSIONS.contains(&extension.as_str()) => {
                UseCase::Research
            }
            _ => UseCase::GeneralProductivity,
        },

        EventSource::Shell | EventSource::Proc => match command_of(event) {
            Some(command) => classify_command(&command),
            None => UseCase::GeneralProductivity,
        },

        EventSource::Editor => UseCase::Coding,
    }
}

fn classify_command(command: &str) -> UseCase {
    let lowered = command.to_lowercase();

    // Build tooling first: `cargo doc` is still coding, even though it is docs.
    if CODING_COMMANDS
        .iter()
        .any(|needle| lowered.contains(needle))
    {
        return UseCase::Coding;
    }

    if RESEARCH_COMMANDS
        .iter()
        .any(|needle| lowered.contains(needle))
    {
        return UseCase::Research;
    }

    // `git` reaches here only when it arrived over the shell rather than a hook.
    if lowered.starts_with("git ") || lowered.starts_with("vim ") || lowered.starts_with("nvim ") {
        return UseCase::Coding;
    }

    UseCase::GeneralProductivity
}

/// Decide how durable this memory is.
///
/// The question being answered is "would this still be worth knowing in a
/// month?" Most observations would not.
pub fn classify_memory_type(event: &RawEvent) -> MemoryType {
    match event.source {
        // A dependency change is a fact about the project that outlives the day
        // it happened: "this project uses tokio 1.40" stays true.
        EventSource::Manifest => MemoryType::Semantic,

        EventSource::Git => match action_of(event).as_deref() {
            // A commit message describes a durable change to the project.
            Some("commit") => MemoryType::Semantic,
            // Switching branches is a moment, not a fact.
            _ => MemoryType::Episodic,
        },

        EventSource::Shell | EventSource::Proc => match command_of(event) {
            Some(command) if is_procedural_command(&command) => MemoryType::Procedural,
            _ => MemoryType::Episodic,
        },

        // Editing a config file says something durable about how the project is
        // set up. Editing a source file is just today's work.
        EventSource::FileSystem => match path_of(event) {
            Some(path) if is_configuration(&path) => MemoryType::Semantic,
            _ => MemoryType::Episodic,
        },

        EventSource::Editor => MemoryType::Episodic,
    }
}

/// Commands that encode "how you do a thing here", which is worth remembering
/// long after the particular invocation is forgotten.
fn is_procedural_command(command: &str) -> bool {
    let lowered = command.to_lowercase();
    [
        "make ",
        "just ",
        "npm run",
        "pnpm run",
        "yarn run",
        "docker compose",
        "docker-compose",
        "kubectl apply",
        "terraform apply",
        "ansible-playbook",
        "./scripts/",
        "migrate",
        "deploy",
    ]
    .iter()
    .any(|needle| lowered.contains(needle))
}

fn is_configuration(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);

    const CONFIG_FILES: &[&str] = &[
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Dockerfile",
        "docker-compose.yml",
        "Makefile",
        "justfile",
        "tsconfig.json",
        ".env",
        "requirements.txt",
        "build.gradle",
        "pom.xml",
    ];

    CONFIG_FILES.contains(&name)
        || matches!(
            extension(name).as_deref(),
            Some("toml" | "ini" | "conf" | "cfg")
        )
}

fn path_of(event: &RawEvent) -> Option<String> {
    event
        .payload
        .get("path")
        .or_else(|| event.payload.get("file"))
        .and_then(|v| v.as_str())
        .map(str::to_owned)
}

fn command_of(event: &RawEvent) -> Option<String> {
    event
        .payload
        .get("command")
        .and_then(|v| v.as_str())
        .map(str::to_owned)
}

fn action_of(event: &RawEvent) -> Option<String> {
    event
        .payload
        .get("action")
        .and_then(|v| v.as_str())
        .map(str::to_owned)
}

fn extension(path: impl AsRef<str>) -> Option<String> {
    let path = path.as_ref();
    let name = path.rsplit('/').next().unwrap_or(path);
    // `.env` is a name, not an extension.
    let (stem, extension) = name.rsplit_once('.')?;
    if stem.is_empty() {
        return None;
    }
    Some(extension.to_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn event(source: EventSource, payload: serde_json::Value) -> RawEvent {
        RawEvent {
            id: "test".to_string(),
            timestamp_ms: 0,
            source,
            payload,
        }
    }

    #[test]
    fn editing_source_files_is_coding() {
        for path in [
            "/repo/src/main.rs",
            "/repo/app/page.tsx",
            "/repo/scripts/run.py",
        ] {
            let classified =
                classify_use_case(&event(EventSource::FileSystem, json!({"path": path})));
            assert_eq!(classified, UseCase::Coding, "{path}");
        }
    }

    #[test]
    fn editing_prose_is_research() {
        assert_eq!(
            classify_use_case(&event(
                EventSource::FileSystem,
                json!({"path": "/repo/docs/design.md"})
            )),
            UseCase::Research
        );
    }

    #[test]
    fn build_tooling_is_coding_even_when_it_builds_docs() {
        for command in ["cargo doc --open", "npm run build", "pytest -x"] {
            assert_eq!(
                classify_use_case(&event(EventSource::Shell, json!({"command": command}))),
                UseCase::Coding,
                "{command}"
            );
        }
    }

    #[test]
    fn looking_things_up_is_research() {
        for command in ["man tar", "curl https://example.com", "rg TODO src/"] {
            assert_eq!(
                classify_use_case(&event(EventSource::Shell, json!({"command": command}))),
                UseCase::Research,
                "{command}"
            );
        }
    }

    #[test]
    fn unrecognised_activity_is_general_productivity() {
        assert_eq!(
            classify_use_case(&event(EventSource::Shell, json!({"command": "ssh prod"}))),
            UseCase::GeneralProductivity
        );
        assert_eq!(
            classify_use_case(&event(
                EventSource::FileSystem,
                json!({"path": "/tmp/notes"})
            )),
            UseCase::GeneralProductivity
        );
    }

    #[test]
    fn git_and_manifest_events_are_always_coding() {
        assert_eq!(
            classify_use_case(&event(EventSource::Git, json!({"action": "commit"}))),
            UseCase::Coding
        );
        assert_eq!(
            classify_use_case(&event(EventSource::Manifest, json!({"file": "Cargo.toml"}))),
            UseCase::Coding
        );
    }

    #[test]
    fn commits_and_dependency_changes_are_durable_facts() {
        assert_eq!(
            classify_memory_type(&event(
                EventSource::Git,
                json!({"action": "commit", "message": "add retry"})
            )),
            MemoryType::Semantic
        );
        assert_eq!(
            classify_memory_type(&event(
                EventSource::Manifest,
                json!({"action": "dependencies_updated"})
            )),
            MemoryType::Semantic
        );
    }

    #[test]
    fn branch_switches_are_just_moments() {
        assert_eq!(
            classify_memory_type(&event(EventSource::Git, json!({"action": "checkout"}))),
            MemoryType::Episodic
        );
    }

    #[test]
    fn task_runners_encode_how_things_are_done_here() {
        for command in [
            "make deploy",
            "npm run migrate",
            "docker compose up",
            "./scripts/release.sh",
        ] {
            assert_eq!(
                classify_memory_type(&event(EventSource::Shell, json!({"command": command}))),
                MemoryType::Procedural,
                "{command}"
            );
        }
    }

    #[test]
    fn an_ordinary_command_is_episodic() {
        assert_eq!(
            classify_memory_type(&event(EventSource::Shell, json!({"command": "cargo test"}))),
            MemoryType::Episodic
        );
    }

    #[test]
    fn config_files_are_semantic_but_source_files_are_not() {
        assert_eq!(
            classify_memory_type(&event(
                EventSource::FileSystem,
                json!({"path": "/repo/Cargo.toml"})
            )),
            MemoryType::Semantic
        );
        assert_eq!(
            classify_memory_type(&event(
                EventSource::FileSystem,
                json!({"path": "/repo/src/main.rs"})
            )),
            MemoryType::Episodic
        );
    }

    #[test]
    fn dotfiles_are_not_mistaken_for_extensions() {
        // ".env" would otherwise parse as an empty stem with extension "env".
        assert_eq!(extension(".env"), None);
        assert_eq!(extension("main.rs"), Some("rs".to_string()));
        assert_eq!(extension("Makefile"), None);
    }

    #[test]
    fn classification_never_panics_on_an_empty_payload() {
        for source in [
            EventSource::Shell,
            EventSource::FileSystem,
            EventSource::Git,
            EventSource::Proc,
            EventSource::Manifest,
            EventSource::Editor,
        ] {
            let empty = event(source.clone(), json!({}));
            let _ = classify_use_case(&empty);
            let _ = classify_memory_type(&empty);
        }
    }
}
