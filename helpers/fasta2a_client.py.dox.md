# fasta2a_client.py DOX

## Purpose

- Own the `fasta2a_client.py` helper module.
- This module connects Rezztor EAgent to external A2A agents.
- Keep this file-level DOX profile synchronized with `fasta2a_client.py` because this directory is intentionally flat.

## Ownership

- `fasta2a_client.py` owns the runtime implementation.
- `fasta2a_client.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `AgentConnection` (no explicit base class)
  - `async get_agent_card(self) -> Dict[str, Any]`
  - `async send_message(self, message: str, attachments: Optional[List[str]]=..., context_id: Optional[str]=..., metadata: Optional[Dict[str, Any]]=...) -> Dict[str, Any]`
  - `async get_task(self, task_id: str) -> Dict[str, Any]`
  - `async wait_for_completion(self, task_id: str, poll_interval: int=..., max_wait: int=...) -> Dict[str, Any]`
  - `async close(self)`
- Top-level functions:
- `async connect_to_agent(agent_url: str, timeout: int=...) -> AgentConnection`: Create a connection to a remote agent.
- `is_client_available() -> bool`: Check if FastA2A client is available.
- Notable constants/configuration names: `_PRINTER`.

## Runtime Contracts

- **`connect_to_agent` reaches only a public address.** The URL comes from the model (`tools/a2a_chat.py`), so before any request it adds `http://` when no scheme is given and passes the URL to `helpers.network.validate_public_http_url`, which raises `UnsafeUrlError` for loopback, private, link-local (cloud metadata), `localhost` names, embedded credentials and non-HTTP schemes (audit 6 M2: it used to reach `http://postgres:5432` and the rest of the Compose network). The `httpx` client never follows redirects, and its transport (`_PinnedTransport`, the httpx form of `helpers/network.py`'s `_PinnedAdapter`) connects every request to the address the check returned -- Host, SNI and the certificate check still name the URL's host -- and refuses a request for any other host, so there is no DNS-rebinding window between the check and the connect (`tests/test_a2a_client_public_only.py`). **Behind an egress proxy.** When the environment names a proxy for the URL's scheme (`HTTP_PROXY`/`HTTPS_PROXY`, the customer compose passes them; `NO_PROXY` honoured, `_proxy_for`), the connection goes through it and the proxy resolves the name: `helpers.network.validate_proxied_http_url` applies the same checks short of resolving (an address written as the host must be public, `localhost` names refused), and `_PinnedTransport` with no address keeps only the host check. Redirects are still not followed and TLS still verifies the agent's host name through the tunnel. Without a proxy the address is pinned as above. A name the proxy can resolve to a private address in its own network is the proxy's policy to refuse. Known ceiling: an agent on a private network cannot be reached (an administrator allow-list would be the way to add one).
- `AgentConnection` sends `A2A_TOKEN` (when set in the environment) to whatever URL it is given.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: filesystem writes, network calls, WebSocket state, settings/state persistence, secret handling, scheduler state.
- Imported dependency areas include: `asyncio`, `helpers.network`, `helpers.print_style`, `typing`, `uuid`.

## Key Concepts

- Important called helpers/classes observed in the source: `PrintStyle`, `AgentConnection`, `PrintStyle.warning`, `agent_url.rstrip`, `httpx.AsyncClient`, `A2AClient`, `TimeoutError`, `connection.get_agent_card`, `RuntimeError`, `agent_url.startswith`, `os.getenv`, `self._http_client.aclose`, `self.close`, `response.raise_for_status`, `response.json`, `self.get_agent_card`, `uuid.uuid4`, `self._a2a_client.send_message`, `self._a2a_client.get_task`, `self.get_task`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- `tests/test_a2a_client_public_only.py`.

## Child DOX Index

No child DOX files.
