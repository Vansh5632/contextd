# Contributing to contextd

Start with the [documentation index](docs/README.md), [current architecture](docs/architecture.md), and [status and limits](docs/status.md). Dated design/validation reports describe their own snapshots; they are not a mandate to retain obsolete runtime behavior.

## Branch and pull request workflow

Use a working branch from `develop`; target `develop` with a pull request. Hotfix branches may start from `main`. Keep direct changes off `main` and `develop`. The repository PR template records this workflow.

```bash
git clone https://github.com/vansh5632/contextd.git
cd contextd
git switch develop
git switch -c docs/your-change
```

If contributing through a fork, clone your fork and configure the canonical repository as `upstream`; update your base before branching. Use `feat/`, `fix/`, `docs/`, or `chore/` names appropriate to the change. Commit only intended files; preserve unrelated local work.

Use conventional commit messages such as:

```text
fix(broker): reject unsupported conclusion clauses
docs: refresh current architecture and setup
```

Open an issue first for significant new features, architecture changes, or new dependencies. Small fixes and documentation improvements can go directly to a PR. Fill out [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md), describe the problem/result, link related issues, and state what was actually checked.

## Implementation standards

- Follow existing Rust workspace structure and conventions; use `rustfmt` and `clippy`.
- Reuse existing helpers and dependencies. Keep changes small and complete across callers, schemas, tests, and docs.
- Keep the runtime local-first, and keep event recording functional when optional embeddings are unavailable.
- Preserve trust-boundary validation, errors, claim provenance/confidence, and replayable claim mutations.
- Add focused tests for new behavior or regressions. Keep tests isolated from personal config, profiles, repositories, and recorded data.
- Never commit credentials, personal context dumps, or generated evaluation runs. CI rejects configured generated-dump and agent-plan paths.
- Do not introduce external model/runtime services without an explicit request. Live-agent evaluation can use external services and requires explicit authorization for spend.

## Checks

Run the checks in [TESTING.md](TESTING.md). Rust CI preflight from the root is:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo check --workspace --all-targets --all-features
cargo test --workspace --all-features
```

CI also runs focused Python extraction/admission tests. Use the [harness instructions](TESTING.md#evaluation-harness) when changing extraction, observations, episodes, or evaluation logic. Distinguish local unit tests, manual protocol checks, and live-agent benchmarks in your PR.

## Documentation

Keep [docs/architecture.md](docs/architecture.md) as the current implementation reference and [docs/status.md](docs/status.md) as the current status/limits reference. Update [docs/README.md](docs/README.md) when adding a guide or report. Preserve dated reports and their numbers; add a status banner or successor link instead of rewriting past results as new verification. Keep existing report paths stable.

## License

The checked-in [LICENSE](LICENSE) is **PolyForm Noncommercial 1.0.0**. See its terms and contact the author for commercial permission. The workspace's `MIT OR Apache-2.0` package metadata currently disagrees with that file; do not infer a relicensing from package metadata or old README text.
