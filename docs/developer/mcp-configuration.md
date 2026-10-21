# Advanced MCP Configuration

Most users should start with [MCP Setup](../guides/mcp-setup.md).

This page is for people who need to paste or review MCP JSON by hand. MCP
architecture and source-linked internals live in
[DeepWiki for Rezztor EAgent](https://deepwiki.com/Rezztor/netops-eagent).

## Basic Shape

Command-based MCP tool:

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "uvx",
      "args": ["mcp-server-sqlite", "--db-path", "/root/db.sqlite"]
    }
  }
}
```

URL-based MCP tool:

```json
{
  "mcpServers": {
    "external-api": {
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

## Common Fields

| Field | Meaning |
| --- | --- |
| `command` | Starts a local MCP tool from a command. |
| `args` | Arguments passed to that command. |
| `url` | Connects to an MCP tool that is already running. |
| `headers` | Optional HTTP headers, often used for authentication. |
| `env` | Optional environment variables for command-based tools. |
| `disabled` | Temporarily turns one MCP entry off. |

Use `command` for local tools and `url` for tools that are already running
somewhere else.

## Docker Addresses

If Rezztor EAgent runs in Docker, remember that "localhost" means the container, not
always your host machine.

| Where the MCP tool runs | Address to use from Rezztor EAgent |
| --- | --- |
| Host machine on macOS or Windows | `host.docker.internal` |
| Another container | The container name on the same Docker network |
| Remote machine | Its reachable HTTPS URL |
| Inside Rezztor EAgent's container | A command-based config |

On Linux, `host.docker.internal` may need extra Docker setup. Running the MCP
tool in the same Docker network is often simpler.

## Safety

- Use MCP tools you trust.
- Keep real API keys out of public screenshots and repositories.
- Prefer project secrets or environment variables for credentials.
- Remove MCP tools you no longer use.

## Related

- [MCP Setup](../guides/mcp-setup.md)
- EAgent CLI Connector
- [DeepWiki for Rezztor EAgent](https://deepwiki.com/Rezztor/netops-eagent)
