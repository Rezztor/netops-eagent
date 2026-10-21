# Architecture

Rezztor EAgent architecture is now documented in
[DeepWiki for Rezztor EAgent](https://deepwiki.com/Rezztor/netops-eagent).

Use DeepWiki when you want source-linked explanations of:

- the agent loop and message flow;
- Web UI internals;
- plugin discovery and lifecycle;
- projects, memory, tools, and scheduler internals;
- backend APIs and WebSocket behavior;
- deployment and runtime structure.

This local page intentionally stays short so the repository does not maintain a
second, stale architecture manual.

## Practical Starting Points

| Goal | Start here |
| --- | --- |
| Install or update Rezztor EAgent | Installation Guide |
| Learn the Web UI | [Usage Guide](../guides/usage.md) |
| Create a focused workspace | [Projects Guide](../guides/projects.md) |
| Connect host files and shell | EAgent CLI Connector |
| Build plugins | [Plugins](plugins.md) |
| Build extensions | [Extensions](extensions.md) |
| Configure MCP | [MCP Configuration](mcp-configuration.md) |

If you are changing core behavior, read the relevant DeepWiki page first, then
inspect the source in this repository before editing.
