# Claude guidance

Read [Agent.md](Agent.md) first; it contains the shared repository rules. Use the [documentation index](docs/README.md), [current architecture](docs/architecture.md), [status](docs/status.md), and [testing guide](TESTING.md) as the maintained references.

For using contextd from Claude or another MCP client, follow [connecting agents](docs/connecting-agents.md). `contextd mcp` is a stdio bridge and needs the daemon already running. The current tools are `context_now`, `search_context`, `recall_similar`, `set_intent`, and `note_claim`.

When working on this repository, treat dated research/validation documents as historical evidence. Component 2.1 is not frozen, and an admitted inferred claim is not a verified fact. Avoid copying an old architecture description into new instructions; update the canonical guide when behavior changes.
