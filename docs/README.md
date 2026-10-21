# Rezztor EAgent Documentation

Internal documentation for the Rezztor EAgent platform.

**Deploying it: [setup/deployment.md](setup/deployment.md)** — Docker Compose on one host,
or Kubernetes with the Helm chart, including OIDC, backups, and what to check when it does
not work.

## Fork decisions and conventions

- **[Test baseline](internal/pytest-baseline.txt)** — the tests that were already failing at the fork point, and what each one turned out to be.

## Using the platform

- **[Usage](guides/usage.md)** — the chat interface, tools, and day-to-day operation.
- **[Agent profiles](guides/agent-profiles.md)** — switching and customizing agent personas.
- **[Projects](guides/projects.md)** — isolating workdir, memory scope, instructions, and secrets per project.
- **[Memory](guides/memory.md)** — how recall works and how to curate it.
- **[Skills](guides/skills.md)** — on-demand playbooks the agent can load.
- **[Network automation](guides/network-automation.md)** — reading and changing switches, routers and firewalls, and what has to happen before a change reaches a device.
- **[Device inventory](guides/inventory.md)** — the device list, how changes are proposed and approved, and the sync to the Ansible executor.
- **[Model presets](guides/model-presets.md)** — configuring chat, utility, and embedding models.
- **[Local model tool use](guides/local-model-tool-use.md)** — running against on-premises models.
- **[Troubleshooting](guides/troubleshooting.md)**

## Integrations

- **[API integration](guides/api-integration.md)** — calling the platform from other applications.
- **[MCP setup](guides/mcp-setup.md)** — connecting MCP servers, and exposing this instance as one.
- **[A2A setup](guides/a2a-setup.md)** — agent-to-agent protocol.
- **[Network automation setup](setup/network-automation.md)** — the second container, the inventory, the credentials, and who is allowed to approve.

## Licensing

- **[Activate your licence](guides/licence-activation.md)** — online, offline, the states, and moving to another server.
- **[Update the appliance](guides/update-the-appliance.md)** — a fix from Rezztor as one signed file: in the browser, on the console, and what happens when it does not start.
- **[What licensing sends, and to whom](guides/licensing-data-we-send.md)** — the exhaustive data-minimisation answer for a customer security review.

## Extending the platform

- **[Architecture](developer/architecture.md)** — the agent loop, contexts, and framework structure.
- **[Plugins](developer/plugins.md)** and **[Creating a plugin](guides/create-plugin.md)**
- **[Extensions](developer/extensions.md)** — lifecycle hook points.
- **[Skills](developer/contributing-skills.md)**
- **[WebSockets](developer/websockets.md)**, **[Notifications](developer/notifications.md)**, **[Connectivity](developer/connectivity.md)**
- **[MCP configuration](developer/mcp-configuration.md)**
- **[Sharing and safety](developer/sharing-and-safety.md)**
- **[Development setup](setup/dev-setup.md)** and **[Deployment](setup/deployment.md)**

## Not documented here

Rezztor EAgent has no desktop launcher, no host CLI connector, and no in-app self-update: nothing installs itself. A hand-installed Compose deployment is upgraded by loading the new images and setting `REZZTOR_VERSION`; only an appliance installed from the ISO has an update tool, which can ask `update.rezztor.com` which updates exist ([Update the appliance](guides/update-the-appliance.md)). Upstream documentation for those features was removed rather than left in place to mislead.
