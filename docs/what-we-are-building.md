# What we are building

contextd helps a new coding agent pick up work without making the developer repeat the whole investigation. It runs as a local background process and gives agents one short page about the current work.

That page can carry the goal, constraints still in force, what a command last did, decisions already made, approaches ruled out, unresolved questions, and the next action. Each claim keeps its origin and confidence. An agent's inference stays an inference.

The repository already implements task and claim storage, current briefings, MCP access, history search, and a handoff extractor. Shell hooks capture commands and exit codes. Agents can explicitly send tool output and conclusions through `contextd observe`; installing a shell hook alone does not capture what an agent learned.

The current investigation is making those conclusions trustworthy and useful. Component 2.1 is **not frozen**: stricter evidence checks reject more false claims, but also drop useful explanations. The latest recorded pool-bug handoff became slower and more expensive than the preceding automatic page. There is no general success guarantee yet.

Start with [build and setup](build-walkthrough.md), then [connect an agent](connecting-agents.md). Read [current status](status.md) for evidence and unfinished work, [architecture](architecture.md) for the implementation, and the [documentation index](README.md) for historical research and reports.
