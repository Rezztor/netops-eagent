# Network Automation

Lets the agent inspect network devices and firewalls through Ansible, and — once
an engineer has approved the exact commands — change them.

**Status: built, not yet verified against hardware.** The tools, the change
gate, the approval flow and the executor all exist and are tested; no playbook
here has touched a real device.

## How it will work

1. The agent asks for a read — facts, interfaces, routes, neighbours, logs, running
   config. That happens immediately; reading needs no approval.
2. For a change, the agent drafts one from the reviewed playbook catalogue. The
   executor runs it in check mode and returns **the exact commands the device would
   receive**.
3. An engineer reads those commands and approves them. They are hashed at approval.
   Everyone who may approve is told the change waits (the bell, and mail when it is
   configured), and the **Approvals** page lists what you may sign, oldest first.
4. On apply, the plan is re-rendered and compared against that hash. If the device
   has drifted, the change is superseded rather than silently applied.

The application never connects to a device. A dedicated Ansible executor holds the
credentials and the inventory, and is the only workload permitted to reach the
management network.

## Setting it up

1. Build and run the Ansible executor (`executor/README.md`), and mount your
   inventory and device credentials into it.
2. In **Settings → Network automation**, use *Check reachability* to confirm
   the application can reach it. The Compose file gives the application the
   executor's address and both tokens (ADR 0036), so there is nothing to type;
   the administration token is what Network → Devices uses and must not be the
   job token. Only a deployment that keeps it away from the application
   (`REZZTOR_ANSIBLE_APP_ADMIN_TOKEN=`) pastes it here.
3. Enable this plugin. It ships switched off, and switches itself on once when
   the application first starts beside a running executor.
4. Grant the network tools to the roles that should have them, and keep
   `network.approve` away from anyone who requests changes — approving your own
   change is refused, and a role holding both is not a control.

## Who may do what

| | Read | Draft a change | Approve | Apply |
|---|---|---|---|---|
| Standard user | — | — | — | — |
| Power user | yes | yes | **no** | yes, once approved |
| Administrator | yes | yes | yes | yes |

## Keeping device data on a local model

**Settings → Network automation → Keep device data on a local model** (`plugins.manage`, off by default) refuses the
network, inventory and SolarWinds tools unless the chat and utility models have a loopback or private API base (or a
bare host name such as `http://ollama:11434`). No API base counts as hosted. A dotted name is never treated as local (ADR 0052).

## What is deferred

Change-record integration — quoting a ServiceDesk CR number and verifying it live
before execution — is designed but not built. See the roadmap section of the plan.
Nothing here calls a service desk.
