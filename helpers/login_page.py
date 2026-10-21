"""The sign-in page.

Owned by the backend rather than by the frontend, because it has to render
before there is a session and therefore before the application bundle is allowed
to load. It is the one page the server draws itself.

Self-contained on purpose. It previously lived in the inherited `webui/`
directory and pulled its stylesheet, fonts and logo from that static folder;
when the directory was removed the page would have rendered unstyled, with
broken images, at the one moment a user has no other way in. Everything it needs
is inline, so it has no assets to lose.

The palette and type scale are the same token set as
`frontend/src/styles/tokens.css`, written out literally here because this page
cannot import from the bundle. That makes it a second copy, and second copies
drift: change a colour there and change it here in the same commit, or sign-in
ends up wearing last quarter's brand. `tests/test_brand_palette_consistency.py`
fails when they diverge, so the drift is caught rather than discovered.

The brand mark, radii, focus treatment and font stack deliberately match the
application rail in `frontend/src/routes/shell.module.css`. Signing in should
feel like the first screen of the product, not a gate in front of it.

The typeface is served from `/app/fonts`, the one unauthenticated slice of the
build (`helpers/spa.py:serve_font`). It is a progressive enhancement, not a
dependency: the stack below it is the system one, so if the build is missing the
page renders correctly in a system face. That is the distinction from the
inherited login page, which lost its entire stylesheet along with its static
folder and rendered as unstyled HTML.
"""

from __future__ import annotations

# Kept as a Jinja template string: `login_handler` renders it with
# `render_template_string`, and the escaping that gives us matters for `next`,
# which comes from the URL.
# The three pages share one stylesheet.
#
# Split rather than copied: sign-in, "forgot password" and "choose a new
# password" are the same moment in the product, and the note above about a
# second copy of the palette drifting applies with more force to a third and
# a fourth. Each page supplies its own title and body; the rest is shared.
_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex, nofollow">
<title>{title} · Rezztor EAgent</title>"""

# The tab icon is inline: a browser otherwise asks for /favicon.ico, which nobody serves, and logs the 404.
_HEAD_REST = """
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2308696e'/%3E%3Ctext x='16' y='23' font-family='system-ui,sans-serif' font-size='20' font-weight='700' text-anchor='middle' fill='%23fff'%3ER%3C/text%3E%3C/svg%3E">
<style>
  /* Self-hosted, so the page makes no outbound request. The variable cut covers
     the whole weight axis in one file, which matters here because the interface
     uses 550 and 650 -- weights that do not exist as static instances. */
  @font-face {
    font-family: "Plus Jakarta Sans";
    font-style: normal;
    font-weight: 200 800;
    font-display: swap;
    src: url("/app/fonts/plus-jakarta-sans-latin.woff2") format("woff2");
  }

  /* Three theme states, matching tokens.css: the bare :root carries the whole
     light palette, and the media query redefines only the tokens. There is no
     theme toggle on this page -- the preference lives in the app, which has not
     loaded yet -- so the system preference is the only signal available. */
  :root {
    --bg: #f2f7f8;
    --surface: #ffffff;
    --surface-alt: #f6fafb;
    --ink: #0c1b1e;
    --ink-2: #405a5e;
    --ink-3: #566d71;
    --line: #dce7e9;
    --line-2: #c4d5d8;
    --accent: #08696e;
    --accent-ink: #ffffff;
    --accent-wash: #e1f2f3;
    --crit: #a32c28;
    --crit-wash: #f9e5e4;
    --font-ui: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI",
      Roboto, sans-serif;
    --text-xs: 11px;
    --text-sm: 12.5px;
    --text-base: 13px;
    --text-md: 14px;
    --radius: 8px;
    --radius-sm: 6px;
    --shadow: 0 1px 2px rgba(12,27,30,.05), 0 8px 24px rgba(12,27,30,.08);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #091517;
      --surface: #101f22;
      --surface-alt: #14272a;
      --ink: #e2eef0;
      --ink-2: #9cb3b6;
      --ink-3: #839b9d;
      --line: #1e3134;
      --line-2: #2c4548;
      --accent: #39b8bd;
      --accent-ink: #041214;
      --accent-wash: #0d262a;
      --crit: #e0736c;
      --crit-wash: #2a1614;
      --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
    }
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 24px;
    background: var(--bg);
    color: var(--ink);
    font-family: var(--font-ui);
    font-size: var(--text-base);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }

  /* A single hairline of brand across the top of the card, echoing the accent
     rail the application shell uses to mark the active surface. */
  .card {
    width: min(392px, 100%);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .card::before {
    content: "";
    display: block;
    height: 3px;
    background: var(--accent);
  }
  .inner { padding: 26px 28px 28px; }

  /* Matches .brand / .brandMark / .brandName in shell.module.css. */
  .brand { display: flex; align-items: center; gap: 10px; }
  .mark {
    width: 30px; height: 30px; border-radius: 7px; flex: none;
    background: var(--accent); color: var(--accent-ink);
    display: grid; place-items: center;
    font-weight: 700; font-size: var(--text-md);
  }
  .name { font-weight: 700; font-size: var(--text-md); letter-spacing: -.01em; }
  .sub { font-size: var(--text-xs); color: var(--ink-3); }

  .lede {
    margin: 20px 0 18px;
    padding-top: 18px;
    border-top: 1px solid var(--line);
    font-size: var(--text-sm);
    color: var(--ink-2);
  }

  .field { margin-bottom: 13px; }
  label {
    display: block; font-size: var(--text-sm); font-weight: 600;
    color: var(--ink-2); margin-bottom: 5px;
  }
  input {
    width: 100%;
    border: 1px solid var(--line-2);
    border-radius: var(--radius-sm);
    padding: 9px 11px;
    background: var(--surface);
    color: var(--ink);
    font: inherit;
    outline: none;
    transition: border-color .12s, box-shadow .12s;
  }
  input::placeholder { color: var(--ink-3); }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-wash); }

  button {
    width: 100%;
    margin-top: 6px;
    border: 1px solid var(--accent);
    border-radius: var(--radius-sm);
    padding: 10px;
    background: var(--accent);
    color: var(--accent-ink);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    transition: filter .12s;
  }
  button:hover { filter: brightness(1.08); }
  button:active { filter: brightness(.94); }

  /* An accent-coloured bar would read as brand; state gets its own colour, the
     same separation tokens.css enforces everywhere else. */
  .error {
    display: flex; gap: 8px; align-items: flex-start;
    margin: 15px 0 0;
    padding: 9px 11px;
    border-radius: var(--radius-sm);
    background: var(--crit-wash);
    color: var(--crit);
    font-size: var(--text-sm);
  }
  .error svg { flex: none; margin-top: 1px; }

  .notice {
    margin: 14px 0 0;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface-2, rgba(127,127,127,.08));
    font-size: 13px;
    line-height: 1.5;
  }
  .alt {
    margin: 14px 0 0;
    text-align: center;
    font-size: 13px;
  }
  .alt a { color: var(--accent); text-decoration: none; }
  .alt a:hover { text-decoration: underline; }
  .foot {
    margin: 20px 0 0;
    padding-top: 15px;
    border-top: 1px solid var(--line);
    font-size: var(--text-xs);
    color: var(--ink-3);
    text-align: center;
  }

  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
  }
</style>
</head>
<body>
  <main class="card">
    <div class="inner">
      <div class="brand">
        <div class="mark" aria-hidden="true">R</div>
        <div>
          <div class="name">Rezztor EAgent</div>
          <div class="sub">Enterprise agent platform</div>
        </div>
      </div>

"""

_FOOT = """
      <p class="foot">Authorised use only. Activity on this system is recorded.</p>
    </div>
  </main>
</body>
</html>
"""


def _page(title: str, body: str) -> str:
    """One card, three moments. `title` is trusted; `body` is a Jinja template."""
    return _HEAD.format(title=title) + _HEAD_REST + body + _FOOT


_LOGIN_BODY = """
      <p class="lede">Sign in to continue.</p>

      <form method="POST" action="/login{% if next %}?next={{ next|urlencode }}{% endif %}">
        {% if next %}<input type="hidden" name="next" value="{{ next }}">{% endif %}
        <input type="hidden" name="login_form" value="{{ login_form }}">
        <div class="field">
          <label for="username">Username</label>
          <input type="text" id="username" name="username" autocomplete="username"
                 autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input type="password" id="password" name="password"
                 autocomplete="current-password" required>
        </div>
        <button type="submit">Sign in</button>
        {% if error %}
        <p class="error" role="alert">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6.6" stroke="currentColor" stroke-width="1.5"/>
            <path d="M8 4.8v3.6M8 11.1h.01" stroke="currentColor" stroke-width="1.6"
                  stroke-linecap="round"/>
          </svg>
          <span>{{ error }}</span>
        </p>
        {% endif %}
      </form>

      <p class="alt"><a href="/forgot-password">Forgot your password?</a></p>

"""

LOGIN_PAGE = _page("Sign in", _LOGIN_BODY)


# --- the two pages a locked-out user sees -----------------------------------
#
# Server-rendered like sign-in, and for the same reason: the application bundle
# is only served to a session, and somebody resetting a password does not have
# one. A React route at /app/reset-password would bounce them to sign-in, which
# is the page they cannot get past.

_FORGOT_BODY = """
      <p class="lede">Enter your username or the email address on your account.</p>

      <form method="POST" action="/forgot-password">
        <input type="hidden" name="login_form" value="{{ login_form }}">
        <div class="field">
          <label for="identifier">Username or email</label>
          <input type="text" id="identifier" name="identifier" autocomplete="username"
                 autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
        </div>
        <button type="submit">Send a reset link</button>
        {% if error %}
        <p class="error" role="alert"><span>{{ error }}</span></p>
        {% endif %}
        {% if notice %}
        <p class="notice" role="status">{{ notice }}</p>
        {% endif %}
      </form>

      <p class="alt"><a href="/login">Back to sign in</a></p>
"""

_RESET_BODY = """
      {% if done %}
      <p class="lede">{{ notice }}</p>
      <p class="alt"><a href="/login">Go to sign in</a></p>
      {% else %}
      <p class="lede">Choose a new password.</p>

      <form method="POST" action="/reset-password">
        <input type="hidden" name="token" value="{{ token }}">
        <div class="field">
          <label for="new_password">New password</label>
          <input type="password" id="new_password" name="new_password"
                 autocomplete="new-password" minlength="{{ min_length }}" required autofocus>
        </div>
        <div class="field">
          <label for="confirm_password">Repeat it</label>
          <input type="password" id="confirm_password" name="confirm_password"
                 autocomplete="new-password" minlength="{{ min_length }}" required>
        </div>
        <button type="submit">Set the new password</button>
        {% if error %}
        <p class="error" role="alert"><span>{{ error }}</span></p>
        {% endif %}
      </form>
      {% endif %}
"""

FORGOT_PAGE = _page("Reset your password", _FORGOT_BODY)
RESET_PAGE = _page("Choose a new password", _RESET_BODY)
