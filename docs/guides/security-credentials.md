# Device credentials and the model

How a device password is kept away from the LLM. Decision record:
[ADR 0044](../internal/decisions/0044-what-the-model-can-see-of-a-device-credential.md).

## Giving the agent a credential

Use the secure form: the Network screen, or the card the agent shows when it needs a
credential. Never type a password into chat. If you do, the input guard masks what looks like
a password before the model sees it and shows you a warning; it is pattern-based, so do not
rely on it.

## Firewall API tokens

A FortiGate REST API token or a Palo Alto API key is a credential of kind `api_token`: stored encrypted like a
password, never returned, never shown to the model. The executor sends it in a request header (`Authorization: Bearer`
or `X-PAN-KEY`), never in a URL, and the call that carries it is `no_log`, so it reaches no job event, log line or
device request log. Create it for a **read-only** administrator and, on a FortiGate, restrict its trusted hosts to the
executor; the steps and links are in [Network automation](network-automation.md#create-the-read-only-api-administrator).
The device certificate is verified on every call. A token that leaks is revoked on the device (delete the REST API
administrator, or regenerate the key), then replaced in the form.

## What the model can and cannot see

| It can see | It cannot see |
| --- | --- |
| Credential name, kind, username, fingerprint | A stored password, key or community string |
| Device output, after the executor scrub and `redact_text` (secrets appear as `[REDACTED:...]`) | The job folder, the encryption key |
| Approved change commands with their operational values (community strings, neighbours) | Anything entered in the secure form |
| Your chat text after the input guard | |

Stored secrets are AES-256-GCM encrypted in the executor, write-only (no route returns one),
and decrypted only into a job's own folder, which is deleted after the job.

## Accepted risks

- The credential key and the ciphertext are on the same host. The key can stay out of `.env` and
  the container's environment (see below), but the executor still holds it in memory.
- Approved commands keep real values by design; do not put passwords in commands.
- The text the model sees goes to whichever LLM provider is configured; there is no
  "local only" restriction yet.

## Configuration backups

A backup of a device's running configuration is stored **encrypted on the executor** (same key as the
credentials, [ADR 0047](../internal/decisions/0047-config-backups-stay-encrypted-on-the-executor.md)) and never
leaves it in the clear over HTTP. The application and the screen get dates, sizes, hashes, a **redacted** view and a
redacted diff; the model gets nothing, because there is no agent tool for backups. A changed secret appears in a diff
as a changed line without its value (a FortiGate or Palo Alto backup is masked the same way: FortiOS `ENC` values, pre-shared keys, SNMP communities, PAN-OS `phash` and key fields). To read a backup unredacted you need a shell on the executor host:
`ea-ansible backup export <host> <id>`. Back up now and viewing a backup need `inventory.manage`.
Set `REZZTOR_ANSIBLE_BACKUP_KEEP` (default 30 per host) and, for a scheduled round,
`REZZTOR_ANSIBLE_BACKUP_INTERVAL_HOURS` (default 0, off). Losing the key makes the stored backups unreadable.

## The executor's socket and the key from a file

The executor listens on a Unix socket (`REZZTOR_ANSIBLE_SOCKET`, mode 0660) in the `ansible_socket` volume and on no
TCP port; the application connects with `REZZTOR_ANSIBLE_EXECUTOR_URL=unix:///run/ansible-socket/executor.sock`
([ADR 0051](../internal/decisions/0051-the-executor-listens-on-a-socket.md)). Only those two containers mount the volume; the
runner does not. The bearer tokens still apply, so a process that can reach the socket still needs one.

`REZZTOR_ANSIBLE_CREDENTIAL_KEY_FILE` (and `..._EXECUTOR_TOKEN_FILE`, `..._ADMIN_TOKEN_FILE`) read the value from a
mounted file; setting both the variable and its `_FILE` is refused. To keep the key out of `.env`, `docker inspect`
and the process environment, use a Docker secret in the compose file:

```yaml
services:
  ansible:
    environment:
      REZZTOR_ANSIBLE_CREDENTIAL_KEY: ""            # must be empty or absent when the _FILE form is used
      REZZTOR_ANSIBLE_CREDENTIAL_KEY_FILE: /run/secrets/ansible_credential_key
    secrets: [ansible_credential_key]
secrets:
  ansible_credential_key:
    file: ./secrets/ansible_credential_key          # chmod 600; head -c32 /dev/urandom | base64 > it
```

Nothing is re-encrypted by moving the key: the file must hold the same value the variable did.

## Network facts (checked 2026-10-02, development stack)

Connect tests from inside the running containers; nothing was changed.

| From | Public host (1.1.1.1:443) | Executor `:8500` | Notes |
| --- | --- | --- | --- |
| Application (`rezztor-eagent`) | reachable | reachable (`ansible_net`; since ADR 0051 over a socket instead) | **No outbound fence.** Networks: `backend` (internal), `runner_net`, `ansible_net`, `searxng_net`. |
| Executor (`rezztor-eagent-ansible`) | reachable | - | Networks: `ansible_net`, plus `eagent-lab` when the lab runs. |
| Runner (`rezztor-eagent-runner`) | reachable | **not reachable** | Only on `runner_net`; the name does not resolve and a direct connect to the executor's address times out. The runner can reach the application. |

On that date the runner's fence against private ranges was an iptables script on the host,
inactive on the development host, and the application and executor had none. Since 2026-10-06
the fence is the network guard, `deploy/customer/rezztor-guard` (ADR 0079): one nftables table
that also fences the search engine and the cloud executor, and the application is no longer on
`ansible_net` (it has its own outbound network, `app_net`). The guard is loaded on the host by
hand (INSTALL.md, step 6); it is not loaded on the development host. `backend` and `cloud_db`
are `internal: true`.

Next steps: an optional local-model mode, an egress allow-list for the application container.
