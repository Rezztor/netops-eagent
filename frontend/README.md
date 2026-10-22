# Rezztor EAgent frontend

React + TypeScript, built with Vite. Talks to the backend over `/api` and is
served **same origin** by the backend at `/app`, so the session cookie and the
CSRF token share an origin with the API.

## Why same origin

Authentication is a Flask session cookie with `SameSite=Lax` plus a CSRF token.
That only works when the page and the API share an origin. Serving the frontend
from a different origin would mean replacing a working auth implementation with
a token scheme, so instead the frontend is built to static files and a proxy puts
it on the same origin as the API.

`vite.config.ts` mirrors that in development: the dev server proxies `/api`,
`/login`, `/logout`, `/socket.io` and `/plugins` to the backend, so development
and production behave identically. `tests/test_frontend_contract.py` fails if a
backend-owned path is missing from that list.

## Running it

```bash
# backend, in the repository root
.venv/bin/python run_ui.py --port 8080

# frontend
cd frontend
npm install
npm run dev          # http://127.0.0.1:5273
```

Point at a different backend with `EAGENT_BACKEND=http://host:port npm run dev`.

```bash
npm run typecheck    # tsc --noEmit
npm run lint         # eslint, zero warnings allowed
npm test             # vitest
npm run build        # typecheck + production build into dist/
```

## Layout

```
src/
  api/
    client.ts      the only module that calls fetch()
    types.ts       mirrors api/bootstrap.py and helpers/rbac.py
  components/      the component library; no component names a colour
  lib/
    session.tsx    session context, useCan(), <Can>
    theme.ts       system / light / dark
  routes/          screens
  styles/
    tokens.css     design tokens; a component never names a colour
    global.css     reset and base
```

## Things worth knowing before changing this

**Every API call goes through `apiRequest`.** The backend's `csrf_protect` does
not exempt safe methods, so *every* authenticated endpoint needs the
`X-CSRF-Token` header including GETs. A bare `fetch()` elsewhere would omit it
and fail as a 403 at the worst moment; a test enforces that no other module
calls `fetch` directly.

**`GET /api/bootstrap` is the cold start.** One request returns session state,
identity, permissions, preferences and the CSRF token. It answers while signed
out — that is how the app knows to show the login page — and returns only
`authenticated: false` and the version to an anonymous caller.

**Permission gating here is usability, not security.** `useCan()` decides what
to render; `helpers/api_policy.py` decides what is allowed, and rejects the
request regardless of what was rendered. Never treat a hidden control as a
protection.

**Tokens define the design.** `styles/tokens.css` is ported from the reference
pages that were reviewed and approved. Components reference tokens only, so both
themes resolve as a set and a palette change is one file. Three theme states, not
two: "system" stamps nothing and lets `prefers-color-scheme` decide.

## Deployment

The backend serves the built bundle at `/app` (`helpers/spa.py`). Build first:

```bash
cd frontend && npm run build     # writes frontend/dist/
```

Which interface answers `/` is one environment variable:

| `REZZTOR_UI` | `/` serves |
|---|---|
| unset or `next` | the built frontend (**default**) |
| `legacy` | the inherited interface |

Both stay reachable either way — `/app` for the new one, `/legacy` for the old —
so switching never strands anyone, and reverting is a restart rather than a
rollback.

**The build is load-bearing.** `frontend/dist/` is gitignored, so an image
without it starts, logs a warning and serves the inherited interface instead —
a deployment that looks like it worked while running the wrong thing. The
Dockerfile runs `docker/run/fs/ins/build_frontend.sh` for exactly this reason;
if you build the image another way, build the frontend too.

## Status

Stages 3 to 6 are done, and the port has reached parity: the new frontend
reaches all 86 API endpoints the inherited interface does. It is now what `/`
serves by default.

Stage 7 — deleting `webui/` — is the remaining step, and is deliberately not
taken yet. Parity means API surface, not that the two interfaces behave alike,
notably the browser's Xpra live view. `tests/test_legacy_ui_removal_readiness.py` turns into
a failing assertion the moment `webui/` is removed while anything still depends
on it.
