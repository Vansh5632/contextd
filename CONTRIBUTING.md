# Contributing to contextd

Thank you for your interest in contributing! This document explains the workflow to follow when making changes.

---

## Branch Workflow

We use a structured branching model:

```
main        ← stable, tagged releases only
  ↑
staging     ← pre-release integration testing
  ↑
develop     ← active development, all PRs target here
  ↑
feat/*  fix/*  docs/*  chore/*  ← your working branches
```

### Rules

- **Never push directly to `main` or `develop`**
- Always branch off `develop` (exception: `hotfix/*` branches off `main`)
- All changes go through a Pull Request

---

## Step-by-Step: Making a Change

### 1. Fork & Clone

```bash
git clone https://github.com/vansh5632/contextd.git
cd contextd
git remote add upstream https://github.com/vansh5632/contextd.git
```

### 2. Create Your Branch

Branch off `develop` using the naming convention:

| Type | Branch Name Example |
|---|---|
| New feature | `feat/shell-watcher` |
| Bug fix | `fix/memory-leak` |
| Documentation | `docs/architecture-update` |
| Chore / config | `chore/setup-linter` |
| Emergency hotfix | `hotfix/critical-crash` (off `main`) |

```bash
git checkout develop
git pull upstream develop
git checkout -b feat/your-feature-name
```

### 3. Make Changes & Commit

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add shell command watcher
fix: resolve memory leak in event buffer
docs: update architecture overview
chore: configure clippy
refactor: simplify importance scorer logic
test: add unit tests for use-case classifier
```

```bash
git add .
git commit -m "feat: add shell command watcher"
```

### 4. Push Your Branch

```bash
git push origin feat/your-feature-name
```

### 5. Open a Pull Request

- Target branch: **`develop`**
- Fill out the PR template completely
- Link any related issues using `Closes #123`

---

## Commit Message Format

```
<type>(<optional scope>): <short summary>

<optional body>

<optional footer: Closes #issue>
```

**Types:** `feat` | `fix` | `docs` | `chore` | `refactor` | `test` | `perf` | `ci`

---

## Code Standards

- Language: **Rust** — follow `rustfmt` and `clippy` rules
- All public functions must have comments
- No external dependencies without prior discussion in an issue
- Keep everything local-first — no cloud calls, no API keys in code

### Local CI Preflight

Before opening a PR, run the same checks used by CI:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo check --workspace --all-targets --all-features
cargo test --workspace --all-features
```

---

## What Needs an Issue First?

Open an issue before starting work on:
- New features or significant changes
- Changes to the core architecture
- New external dependencies

Small bug fixes and docs improvements can go straight to a PR.

---

## Commercial Use

This project is licensed under the **PolyForm Noncommercial License 1.0.0**.  
Commercial use requires explicit permission from the author.  
See [LICENSE](LICENSE) for details.
