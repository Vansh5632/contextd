# AGENT.md

**Project:** contextd  
**Version:** 0.1 (Implementation Phase)

## Project Goal

contextd is a lightweight, fully local context broker daemon that gives every AI coding agent on a developer’s machine a single shared source of truth about “what the developer is doing right now.”

It eliminates the need for humans to copy-paste context between tools.

## Core Product Vision

- One local daemon (`contextd`)
- Watches shell, editor, filesystem, git, processes, and project manifests in real time
- Maintains a live, structured context snapshot
- Exposes it via MCP and Unix socket
- Fully offline, privacy-first, single static Go binary

## Finalized System Design Highlights

**Use-Case Aware Routing**  
Every event is classified immediately (`coding`, `research`, `general_productivity`, etc.) and routed with different processing rules.

**4-Tier Memory System**
- Tier 0 → Working Memory (in-memory, live snapshot)
- Tier 1 → Short-term (SQLite + vector)
- Tier 2 → Mid-term (vector + knowledge graph – semantic + procedural memory)
- Tier 3 → Long-term Archive (compressed)

**Real-Time Pipeline**
Raw Event → Use-Case Classifier → Importance Scorer → Content Processor → Memory Type Classifier → Decision Engine → Correct Tier

**Background Layer**
Consolidation, summarization, graph building, and intelligent pruning run asynchronously.

## Repository Structure (Planned)
contextd/
├── cmd/contextd/          # main entrypoint
├── internal/
│   ├── config/
│   ├── event/
│   ├── pipeline/          # hot path
│   ├── memory/            # all 4 tiers
│   ├── background/        # async workers
│   ├── broker/
│   ├── sources/           # shell, editor, fs, git, etc.
│   ├── api/               # MCP + socket
│   ├── ollama/
│   └── daemon/

## How Agents Should Interact With This Project

- You are helping build the **shared context infrastructure layer**, not a single AI tool.
- Prioritize: correctness, performance (<50 ms hot path), privacy, and clean single-binary distribution.
- All AI calls must go through local Ollama only.
- Never suggest cloud services or external APIs unless explicitly asked.

**Current Phase:** We have just locked the complete architecture and are beginning clean Go implementation.

Read `claude.md` for additional Claude-specific guidance.

This file is the canonical source of truth for any AI agent working in this repository.

