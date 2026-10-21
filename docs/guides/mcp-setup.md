# MCP Setup

MCP lets Rezztor EAgent use tools from other apps and services.

Think of each MCP connection as a bridge. One bridge might connect Gmail,
another might connect a database, and another might connect an automation app.

Use MCP when you have a clear external tool you want Rezztor EAgent to call.

> [!NOTE]
> This page is about giving Rezztor EAgent tools from other apps. For deeper MCP
> details, see the [advanced MCP reference](../developer/mcp-configuration.md).

## When To Use MCP

| Need | Good first stop |
| --- | --- |
| Connect a third-party app or service with MCP support | This guide |
| Paste or review MCP JSON by hand | [Advanced MCP Configuration](../developer/mcp-configuration.md) |

## Before You Add One

- [ ] You know what app or service you want to connect.
- [ ] You trust the package or URL.
- [ ] You know where it will run: inside Rezztor EAgent, on your computer, or online.
- [ ] You have any needed credentials ready.
- [ ] You know whether the tool should be project-specific or global.

## Open MCP Settings

1. Click **Settings** in the sidebar.
2. Open the **MCP/A2A** tab.
3. Find **External MCP Servers**.
4. Click **Open**.


## Add A Connection

The configuration editor accepts JSON. A command-based MCP connection looks like
this:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    }
  }
}
```


Click **Apply now** after editing.

> [!TIP]
> The first launch of an `npx` or `uvx` server can take a little longer because
> the package may need to download.

## Check That It Connected

After applying the config, look for the status below the editor.

| Signal | What it means |
| --- | --- |
| Name | The connection Rezztor EAgent found. |
| Tool count | How many tools are available. |
| Green status | The connection is working. |
| Error text | The command, URL, network, or credentials need attention. |

After the connection works, Rezztor EAgent discovers the MCP tools. The active
Agent Profile's MCP policy still decides which discovered tools it may use. Use
**Edit agent -> MCPs** to keep the category default or set an individual tool
to **On** or **Off**. See [Agent Profiles](agent-profiles.md#choose-capability-access).

You can still ask naturally:

```text
Use the connected Gmail tools to find the last message from Alice and summarize it.
```

## Common Examples

### Tool Started By A Command

Use this pattern when Rezztor EAgent should start the tool itself.

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

### Tool At A URL

Use this pattern when the tool is already running at a URL.

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

> [!IMPORTANT]
> Do not paste real API keys into public files, screenshots, or issue reports.
> Prefer project secrets or environment variables when possible.

## Docker Networking

If Rezztor EAgent runs in Docker and the MCP tool runs somewhere else, the address
matters.

| Where the MCP tool runs | What to use from Rezztor EAgent |
| --- | --- |
| Host machine on macOS or Windows | `host.docker.internal` |
| Another container | Same Docker network plus the container name |
| Remote server | The reachable HTTPS URL |
| Inside Rezztor EAgent's container | Local command config |

On Linux, `host.docker.internal` is not always available by default. Running the
MCP tool in the same Docker network is usually cleaner.

## Recommended Server Types

| Tool type | Useful for |
| --- | --- |
| n8n MCP | Workflow automation |
| Gmail MCP | Email workflows |
| VS Code MCP | IDE-centered workflows |

## Troubleshooting

- **No tools appear:** confirm the JSON is valid and click **Apply now** again.
- **Command not found:** install the command where Rezztor EAgent can run it, or use a URL-based tool instead.
- **Package launch is slow:** wait for the first package download to finish.
- **Host service unreachable:** check Docker networking and try `host.docker.internal` on macOS or Windows.
- **Credentials fail:** rotate or re-enter the credential, then restart or reapply the config.

## Related

- EAgent CLI Connector: host-machine access.
- [Advanced MCP Configuration](../developer/mcp-configuration.md): complete configuration reference.
