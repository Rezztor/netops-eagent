# helpers/login_throttle.py

## Purpose

- Limit how fast the local sign-in form can be guessed at, without giving anyone
  a way to lock another person out of their account.

## Contracts

- `retry_after(address, username)` returns the seconds a caller must wait, `0`
  when it may try now. Called **before** authenticating, so a blocked caller
  costs a dictionary lookup instead of a bcrypt hash.
- `record_failure(address, username)` counts one failure; `record_success(...)`
  forgets that caller's failures, including the address counter.
- `reset()` clears everything and exists for tests.
- Thresholds: `ADDRESS_ATTEMPTS` (10) per address, `IDENTITY_ATTEMPTS` (5) per
  address-and-username pair, both over `WINDOW_SECONDS` (15 minutes), with at
  most `MAX_TRACKED` keys retained.
- The address is `request.remote_addr`. uvicorn rewrites it from `X-Forwarded-For` (first hop that is not a trusted proxy) only when the peer is listed in `FORWARDED_ALLOW_IPS` (default loopback); otherwise it is the peer. A deployment behind a proxy on another host should set that variable.
- **Unnamed proxy.** When `FORWARDED_ALLOW_IPS` is empty or unset and the peer is loopback or this host's default gateway (`_default_gateway`, read once from `/proc/net/route`: in the container, the Docker bridge gateway a proxy on the Docker host arrives as), the peer may be a proxy every browser arrives through, so the `addr:` counter is skipped and only the address-and-username pair is counted. Counting it let ten wrong passwords from anyone lock the form for the whole instance. The cost: in that state, spraying many usernames from one address is slowed only by the one-second delay, and five failures on one username lock that username for everyone behind the proxy for the window. Naming the proxy restores the address counter. Any other peer, a private LAN address included, is a browser and is counted; a proxy on another host that is not named in `FORWARDED_ALLOW_IPS` is therefore counted as one caller. `tests/test_login_throttle.py::test_an_unnamed_proxy_is_not_one_shared_address` covers this.

## Key Concepts

- **Nothing is keyed by username alone, deliberately.** Counting failures per
  account means anyone who knows a colleague's username can lock them out of it
  by failing on purpose, which turns a login form into a denial of service
  against a named person. The address is what an attacker has to spend, so the
  address is what is counted.
- What it replaces was `await asyncio.sleep(1)` after a failed password: a delay
  on one connection, with no counter and nothing to stop the next hundred
  attempts arriving in parallel. The sleep is still there -- it slows a
  sequential guesser, the counter stops a parallel one, and neither does the
  other's job.
- ADR 0021 leaves account lockout to the identity provider, and for an OIDC
  deployment that remains right. This covers the local form, which is
  break-glass there and the only way in for a deployment with no IdP.
- **State is in memory and resets on restart.** This deployment is one host by
  construction and horizontal scaling is documented as unsupported, so there is
  no shared cache to use; a database write per failed attempt would hand an
  attacker a cheaper way to load the database than to guess the password. It is
  a speed bump against online guessing, not a record -- the record is the
  `auth.signin` audit event, emitted either way, plus `outcome: throttled` when
  this refuses.

## Work Guidance

- Keep the checks cheap and before authentication; the point is to avoid work,
  not to add it.
- Do not add a username-only counter without solving the lockout problem above.
- If this ever needs to survive a restart or span hosts, that is a decision
  about deployment shape, not a change to make quietly here.
