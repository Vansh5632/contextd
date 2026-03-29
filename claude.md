# CLAUDE.md

**Project:** contextd  
**One-liner:** A fully local, Linux-first context broker that turns your entire development machine into one shared cognitive workspace for every AI coding agent you use.

## What We Are Building

contextd is **not** another AI coding assistant.  
It is the **neutral infrastructure layer** that sits underneath Cursor, Claude, Continue.dev, Windsurf, terminal agents, etc.

It solves the core fragmentation problem: every AI tool is blind to what the others know. contextd watches your shell, editor, filesystem, git, processes, and project files in real time and maintains a single, always-up-to-date, structured "what am I doing right now" context snapshot that any agent can instantly read via MCP or Unix socket.

Everything runs 100% locally. No cloud. No API keys. Single Go binary + systemd user service.

## Core Architecture (Finalized)

- **Use-case aware** from the first millisecond (coding / research / general productivity / etc.)
- **4-tier hierarchical memory system** (inspired by MemGPT + Mem0 + CoALA + MemoryOS 2025)
  - Tier 0: Working Memory (in-RAM live snapshot)
  - Tier 1: Short-term (SQLite + vector, 24-48h)
  - Tier 2: Mid-term (vector + knowledge graph – semantic + procedural)
  - Tier 3: Long-term Archive (compressed blobs)
- **Real-time hot path** → Use-case classifier → Importance scorer → Content processor → Memory-type classifier → Decision engine
- **Background intelligence layer** (consolidation, summarization, graph building, pruning)
- **Public interfaces**: MCP server + Unix domain socket (`/context/now`)

## Current Status (March 2026)

We have finalized:
- Full system design (use-case routing + 4-tier memory)
- Go tech stack decision
- Event sources strategy
- Memory pipeline flowchart

We are now starting clean implementation of the Go daemon.

## Tech Stack (Locked)

- Core daemon: Go (single static binary)
- Local AI: Ollama (Phi-3 Mini + nomic-embed-text)
- Storage: SQLite + pure-Go vector similarity
- VS Code extension: TypeScript (thin client)
- Shell/Git hooks: Bash
- Protocol: MCP (Model Context Protocol) + custom Unix socket

## How You Should Help Me

When I ask you to write code:
1. Always respect the finalized architecture above.
2. Keep everything local-first and single-binary friendly.
3. Prefer pure Go where possible.
4. Follow the internal package structure we will define.
5. Think in terms of performance, privacy, and zero-friction install.

This is the foundation of what will become a major developer infrastructure product. Treat every line you write with that level of seriousness.