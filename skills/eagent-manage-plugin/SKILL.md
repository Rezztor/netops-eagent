---
name: eagent-manage-plugin
description: "Manage Rezztor EAgent plugins: list what is installed, enable or disable a plugin globally or per project/agent profile, and understand plugin scoping. Use when asked to list, enable, disable, turn on, or turn off a plugin."
version: 2.0.0
tags: ["plugins", "enable", "disable", "activation", "manage", "list"]
trigger_patterns:
  - "list plugins"
  - "enable plugin"
  - "disable plugin"
  - "turn on plugin"
  - "turn off plugin"
  - "which plugins are installed"
  - "plugin not working"
---

# Rezztor EAgent Plugin Management

Rezztor EAgent ships a fixed set of plugins. **There is no plugin installation.**
Installing plugins from Git, ZIP or a community index was removed deliberately:
this product executes code, and a plugin is code, so adding one is a change to
the product rather than a runtime action. If a capability is missing, the answer
is a change to this repository — not an install command.

If a user asks you to install, download, scan or update a plugin, tell them
plainly that this deployment does not install plugins, and point them at whoever
owns the platform. Do not attempt to work around it with shell commands.

## Action routing

| User need | Section |
|---|---|
| See what is installed | [List plugins](#list-plugins) |
| Enable or disable a plugin | [Activation](#activation) |
| Plugin not loading / crashing | Read `/eagent/skills/eagent-debug-plugin/SKILL.md` |
| Build a new plugin | Read `/eagent/skills/eagent-create-plugin/SKILL.md` |

---

## List plugins

Bundled plugins live in `/eagent/plugins/`; anything added for this instance
lives in `/eagent/usr/plugins/`.

```bash
ls /eagent/plugins/
ls /eagent/usr/plugins/ 2>/dev/null
```

In the interface: open **Settings → Plugins**, which lists both with their
current state. That page requires the `plugins.manage` permission, so an account
without it will not see it.

---

## Activation

Plugins are enabled and disabled with toggle files:

- `.toggle-1` — explicitly on
- `.toggle-0` — explicitly off
- no file — the plugin's own default

**Enable**:
```bash
rm -f /eagent/usr/plugins/<name>/.toggle-0
touch /eagent/usr/plugins/<name>/.toggle-1
```

**Disable**:
```bash
rm -f /eagent/usr/plugins/<name>/.toggle-1
touch /eagent/usr/plugins/<name>/.toggle-0
```

In the interface: **Settings → Plugins**, then the toggle beside the plugin.

Plugins declaring `always_enabled: true` in `plugin.yaml` cannot be toggled.
Those are framework core — the audit plugin, for instance, is always on by
design, because a disableable audit trail is not an audit trail.

### Scoped activation

When a plugin declares `per_project_config` or `per_agent_config`, it can be
toggled for one project or one agent profile rather than globally. Place the
toggle file in the scoped path:

- project: `project/.eaproj/plugins/<name>/.toggle-1`
- agent profile: `usr/agents/<profile>/plugins/<name>/.toggle-1`

---

## References

- Plugin architecture: `/eagent/plugins/AGENTS.md`
- Developer lifecycle guide: `/eagent/docs/developer/plugins.md`
- Debug a broken plugin: `/eagent/skills/eagent-debug-plugin/SKILL.md`
- Create a new plugin: `/eagent/skills/eagent-create-plugin/SKILL.md`
- Review a plugin: `/eagent/skills/eagent-review-plugin/SKILL.md`
