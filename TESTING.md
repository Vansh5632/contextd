# Testing

Use checks appropriate to the change. This guide lists commands; it does not claim they have just passed. Historical benchmark results belong to the [dated validation reports](docs/README.md#historical-research-designs-and-reports).

## Rust workspace

From the repository root:

```bash
cargo test --workspace --all-features
```

Run a focused crate or subsystem while developing:

```bash
cargo test -p sources
cargo test -p memory --lib project::tests
cargo test -p broker --lib extract::tests
cargo test -p broker --lib snapshot::tests
cargo test -p store --test integration
```

Tests live beside source in `#[cfg(test)]` modules, plus [store integration tests](crates/store/tests/integration.rs). Shared fixtures are in [core test_utils](crates/core/src/test_utils.rs). Tokio tests cover asynchronous behavior; database/filesystem tests use isolated stores and temporary paths. Follow the surrounding tests rather than requiring a running personal daemon.

Important contracts include task identity and failure supersession, standing constraints, claim origin/confidence validation, extraction evidence and retractions, snapshot selection, socket parsing, hook generation, and persistence. The shell-listener tests exercise parsing/broadcast helpers as well as protocol handling; some avoid binding a real Unix socket. Passing these is not a live editor integration test.

## CI preflight

[The workflow](.github/workflows/ci.yml) currently runs:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo check --workspace --all-targets --all-features
cargo test --workspace --all-features
```

CI also rejects newly added generated dumps and agent plans under the configured paths, and runs the focused Python suite below. Do not commit generated `eval/component0/runs/` outputs or `docs/superpowers/` plans.

## Evaluation harness

The Python harness is in [eval/component0](eval/component0/). It requires Python **3.12+**. From the repository root, create an isolated environment using the dependencies declared in [pyproject.toml](eval/component0/pyproject.toml):

```bash
cd eval/component0
python3 -m venv .venv
.venv/bin/python -m pip install pytest==9.1.1 pyyaml==6.0.3 tiktoken==0.14.0
```

Run the same extraction/admission subset as CI:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_extract_claims.py tests/test_import_trajectory.py \
  tests/test_arms.py tests/test_natural_trajectory.py -q --tb=line -p pytest
```

Additional focused evidence/episode checks:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_retry_budget_live.py tests/test_contextd_packet.py -q
```

Despite its name, `test_retry_budget_live.py` tests local observation/handoff behavior; it is not the paid live-agent matrix. Some extraction/replay tests invoke Cargo and the daemon in isolated temporary workspaces and can write ignored evaluation artifacts. For the complete host harness suite:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ./scripts/host_pytest.sh tests -q
```

The wrapper uses `.venv/bin/python` and clears host `PYTHONPATH`. Docker/image-dependent tests can need local images or fixtures; inspect failures/skips rather than reporting a suite as reproduced from its historical count. Existing validation notes may give counts for older trees.

## Live-agent evaluation

A live matrix is a separate experiment with Docker/image pins, episode workspaces, agent/model access, and potential external-model spend. It is not required for ordinary local checks and must not be launched without explicit authorization. The harness exposes `--i-accept-spend` for live matrix runs.

Before an authorized run, inspect the CLI without starting trials:

```bash
cd eval/component0
.venv/bin/python -m harness.run_matrix --help
```

Use the exact episode, arms, image pins, and run ID required by the experiment. Current episodes include `limiter-handoff` and `retry-budget`. [Component 0 evaluation](docs/component-0-evaluation.md) explains the evaluation method; [Component 2.1 validation](docs/component-2.1-validation.md) records its specific commands and numbers. Generated run artifacts may be absent from a checkout and are not published test fixtures.

## Component 2.1 contract

The Component 2.1 suite scores an unmodified daemon. A failing case is a measurement, not a build failure. The fast tests below do not start the daemon. They check the grader, the frozen cases, the held-out checksum, and the recorded baseline file.

```bash
cd eval/component21
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --tb=line -p pytest
```

To rebuild the daemon and rewrite the baseline:

```bash
cargo build -p contextd --bin contextd
cd eval/component21
python -m harness.run --output baseline/results.json --with-historical
```

## Manual protocol and setup checks

[Setup](docs/build-walkthrough.md#smoke-check) gives a disposable smoke sequence. [Connecting agents](docs/connecting-agents.md#check-mcp-discovery) includes MCP discovery and a direct socket request. Running `contextd install` or the daemon can write shell/Git integrations, so do not use your real profiles/repository as an accidental test fixture.

For documentation-only changes, check relative links and anchors, compare command/schema examples to source or `contextd help`, and run `git diff --check`. A full Rust suite or paid benchmark does not validate prose accuracy by itself.
