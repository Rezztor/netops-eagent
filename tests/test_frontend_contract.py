"""The frontend's hand-written types must match the backend they describe.

`frontend/src/api/types.ts` mirrors `BootstrapResponse` in `api/bootstrap.py`
and the permission strings in `helpers/rbac.py`. Nothing at build time connects
the two -- TypeScript cannot see Python -- so a field renamed on one side would
be caught only by someone noticing an undefined at runtime.

These are deliberately structural, not a full schema comparison: they pin the
field names and the permission list, which is where drift actually happens.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import rbac  # noqa: E402

FRONTEND = PROJECT_ROOT / "frontend"
TYPES_TS = FRONTEND / "src" / "api" / "types.ts"

pytestmark = pytest.mark.skipif(
    not TYPES_TS.is_file(), reason="frontend scaffold not present"
)


def _ts() -> str:
    return TYPES_TS.read_text(encoding="utf-8")


def test_every_permission_is_declared_in_the_frontend():
    """A permission the frontend does not know about cannot gate anything."""
    ts = _ts()
    missing = [p for p in rbac.ALL_PERMISSIONS if f'"{p}"' not in ts]
    assert not missing, f"frontend PERMISSIONS is missing: {missing}"


def test_the_frontend_declares_no_permission_the_backend_lacks():
    """The reverse: a stale permission string silently gates on nothing."""
    ts = _ts()
    block = ts[ts.index("export const PERMISSIONS"): ts.index("} as const;")]
    # Two or more dotted segments: "network.change.draft" has three, and a
    # single-dot pattern skipped it silently -- so a typo in a three-part
    # permission was invisible to this check rather than caught by it.
    declared = set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', block))
    unknown = sorted(declared - set(rbac.ALL_PERMISSIONS))
    assert not unknown, f"frontend declares unknown permissions: {unknown}"


def test_every_role_is_known_to_the_frontend():
    ts = _ts()
    match = re.search(r"export type Role =([^;]+);", ts)
    assert match, "Role union not found in types.ts"
    # Underscores included: role ids like "netops_viewer" were invisible to a
    # pattern that only allowed letters.
    declared = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert declared == set(rbac.ROLES), f"{declared} != {set(rbac.ROLES)}"


def test_bootstrap_response_fields_match_the_pydantic_model():
    """Field names, not types: a rename on either side breaks the frontend."""
    from api.bootstrap import BootstrapResponse

    ts = _ts()
    interface = ts[ts.index("export interface BootstrapResponse"):]
    interface = interface[: interface.index("}")]
    for field in BootstrapResponse.model_fields:
        assert re.search(rf"\b{re.escape(field)}\??:", interface), (
            f"BootstrapResponse.{field} has no counterpart in types.ts"
        )


def test_nested_bootstrap_models_match_too():
    from api.bootstrap import (
        BootstrapLicence,
        BootstrapPreferences,
        BootstrapRuntime,
        BootstrapUpdate,
        BootstrapUser,
        BootstrapVersion,
    )

    ts = _ts()
    for model, name in (
        (BootstrapVersion, "BootstrapVersion"),
        (BootstrapUser, "BootstrapUser"),
        (BootstrapRuntime, "BootstrapRuntime"),
        (BootstrapPreferences, "BootstrapPreferences"),
        (BootstrapLicence, "BootstrapLicence"),
        (BootstrapUpdate, "BootstrapUpdate"),
    ):
        block = ts[ts.index(f"export interface {name}"):]
        block = block[: block.index("}")]
        for field in model.model_fields:
            assert re.search(rf"\b{re.escape(field)}\??:", block), (
                f"{name}.{field} has no counterpart in types.ts"
            )


def test_the_dev_proxy_covers_every_path_the_backend_owns():
    """Option A depends on the frontend and API sharing an origin. Any backend
    path the dev proxy does not forward is a route that works in production and
    404s in development, which is a confusing class of bug to debug."""
    config = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    for path in ("/api", "/login", "/logout", "/socket.io"):
        assert f'"{path}"' in config, f"dev proxy does not forward {path}"
    # /plugins was forwarded for the inherited interface's plugin-owned assets.
    # Those are gone, and forwarding a path nothing serves only hides mistakes.
    assert '"/plugins"' not in config


def test_the_frontend_never_calls_fetch_outside_the_api_client():
    """Auth is a cookie plus a CSRF header on every request. A bare fetch()
    elsewhere would omit the header and fail as a 403 at the worst moment."""
    allowed = {
        FRONTEND / "src" / "api" / "client.ts",
    }
    offenders = []
    for path in (FRONTEND / "src").rglob("*.ts*"):
        if path in allowed or "__tests__" in path.parts:
            continue
        if re.search(r"\bfetch\s*\(", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, f"direct fetch() outside the API client: {offenders}"


def test_admin_screens_are_gated_on_permissions_the_backend_enforces():
    """Every permission the navigation gates on must be one api_policy actually
    checks, or the frontend is hiding screens for a reason the backend does not
    share -- and the reverse, showing a screen whose endpoints will 403."""
    from helpers import api_policy

    nav = (FRONTEND / "src" / "routes" / "nav.tsx").read_text(encoding="utf-8")
    gated = set(re.findall(r"permission: PERMISSIONS\.(\w+)", nav))
    # PERMISSIONS keys are camelCase; map back to the permission strings.
    types_ts = _ts()
    lookup = dict(re.findall(r"(\w+): \"([a-z_]+\.[a-z_]+)\"", types_ts))
    enforced = set(api_policy.API_PERMISSIONS.values())

    # Tool permissions are enforced in helpers/tool_permissions.py when the agent
    # calls the tool, not by the endpoint table.
    from helpers import rbac

    tool_layer = {
        rbac.PERM_CHAT_USE,
        rbac.PERM_TOOL_CODE_EXECUTION,
        rbac.PERM_TOOL_SUBORDINATES,
    }

    for key in gated:
        permission = lookup.get(key)
        assert permission, f"nav gates on PERMISSIONS.{key}, which types.ts does not define"
        if permission in tool_layer:
            continue
        assert permission in enforced, (
            f"nav hides screens behind {permission}, but no endpoint requires it"
        )


def test_the_audit_screen_never_calls_its_data_the_audit_record():
    """The retained trail is the stdout stream shipped off-box; this endpoint is
    a capped in-memory tail. Presenting it as the record would be a compliance
    claim the product cannot support."""
    handler = (PROJECT_ROOT / "plugins" / "_audit" / "api" / "audit_recent.py").read_text(
        encoding="utf-8"
    )
    assert '"is_complete_record": False' in handler
    screen = (FRONTEND / "src" / "routes" / "ActivityScreen.tsx").read_text(encoding="utf-8")
    assert "not the audit record" in screen.lower()


def test_the_recent_event_buffer_is_bounded():
    """An unbounded buffer of every event is a memory leak with an audit label."""
    from plugins._audit.helpers import audit

    assert audit.RECENT_EVENT_LIMIT > 0
    assert audit._recent.maxlen == audit.RECENT_EVENT_LIMIT


def test_every_navigation_icon_goes_through_the_shared_helper():
    """A hand-rolled <svg> in a nav entry renders unsized.

    The helper sets width, height and `flex: none`; an SVG written inline
    without them stretches to fill the row and pushes the label sideways. It
    typechecks, lints, builds and looks completely fine in the source -- the only
    way to notice is to look at the screen, which is exactly why this is a test.
    """
    nav = (PROJECT_ROOT / "frontend" / "src" / "routes" / "nav.tsx").read_text(encoding="utf-8")

    entries = nav.count("icon: ")
    through_helper = nav.count("icon: icon(")
    # One "icon: " belongs to the NavItem type declaration, not to an entry.
    assert entries - through_helper == 1, (
        "a navigation entry defines its icon without the icon() helper, so it "
        "will render at whatever size the row gives it"
    )

    # And the helper must keep constraining it.
    helper = nav[nav.index("const icon = ") :]
    helper = helper[: helper.index("\n);")]
    for constraint in ('width="16"', 'height="16"', 'flex: "none"'):
        assert constraint in helper, f"the icon helper no longer sets {constraint}"


def test_no_guide_documents_a_feature_that_was_removed():
    """Documentation for something that no longer exists is worse than none.

    Someone reads it, tries it, and concludes the product is broken rather than
    that the page is stale. These particular terms describe capabilities removed
    during the fork -- the desktop and Office plugins (ADR 0008), the messaging
    bridges, and the public tunnel (Phase 2) -- and none should reappear in a
    user-facing guide.
    """
    removed = {
        "xfce": "the desktop plugin was removed",
        "libreoffice": "the office plugin was removed",
        "flare tunnel": "the tunnel was removed in Phase 2",
        "serveo": "the tunnel was removed in Phase 2",
        "trycloudflare": "the tunnel was removed in Phase 2",
        "self-update": "instances are upgraded by rolling a new image",
    }
    offenders = []
    for path in (PROJECT_ROOT / "docs").rglob("*.md"):
        # Decision records describe what was removed and why; that is their job.
        if "internal" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").lower().splitlines():
            for term, why in removed.items():
                if term not in line:
                    continue
                # Saying a feature is absent is the opposite of documenting it,
                # and docs/README.md does exactly that on purpose.
                if any(w in line for w in ("no ", "not ", "removed", "no longer")):
                    continue
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: '{term}' — {why}")
    assert not offenders, "guides document removed features:\n  " + "\n  ".join(offenders)


def test_guides_do_not_link_to_pages_that_do_not_exist():
    """A dead internal link is the cheapest possible signal that a guide was left
    behind by a change, and the easiest to check."""
    import re

    broken = []
    for path in (PROJECT_ROOT / "docs").rglob("*.md"):
        for target in re.findall(r"\]\(([^)#:]+\.md)[^)]*\)", path.read_text(encoding="utf-8")):
            if not (path.parent / target).resolve().is_file():
                broken.append(f"{path.relative_to(PROJECT_ROOT)} -> {target}")
    assert not broken, "dead links in documentation:\n  " + "\n  ".join(broken)


def test_a_locally_run_model_is_not_reported_as_needing_a_key():
    """The default embedding model runs on this machine and uses no key.

    models.py routes `sentence-transformers/*` under the huggingface provider to
    LocalSentenceTransformerWrapper: it downloads once and never calls an API.
    Judging usability by key presence alone showed the shipped embedding model as
    unusable when it works, which sends an operator hunting for a credential they
    do not need -- or switching to a provider that cannot embed at all.
    """
    screen = (PROJECT_ROOT / "frontend/src/routes/ModelsScreen.tsx").read_text(encoding="utf-8")
    assert "runsLocally" in screen, "the key check does not know about local models"
    assert 'startsWith("sentence-transformers/")' in screen

    # And the backend must still agree that this is the local path. Whitespace
    # is normalised because the condition in models.py is wrapped across lines.
    models_py = " ".join(
        (PROJECT_ROOT / "models.py").read_text(encoding="utf-8").split()
    )
    assert "LocalSentenceTransformerWrapper" in models_py
    assert 'model_name.startswith( "sentence-transformers/" )' in models_py, (
        "models.py no longer routes sentence-transformers models locally, so the "
        "frontend's assumption that they need no key is now wrong"
    )


def test_the_model_editor_calls_the_endpoint_that_writes():
    """model_config_set was reachable from the fork onwards with no caller.

    Without it the Models screen could show the configuration and swap between
    presets but not change a model, so anything a preset did not cover meant
    editing usr/settings.json by hand and restarting.
    """
    screen = (PROJECT_ROOT / "frontend/src/routes/ModelsScreen.tsx").read_text(encoding="utf-8")
    client = (PROJECT_ROOT / "frontend/src/api/models.ts").read_text(encoding="utf-8")
    assert "setModelConfig" in screen and "setModelConfig" in client
    assert "model_config_set" in client


def test_model_search_results_are_named_objects_with_optional_ctx():
    """The catalogue is a list of model ids plus a context window when known.

    Treating entries as bare strings left the editor unable to auto-fill
    context length, and typing them as `{id}` objects once produced an empty
    autocomplete because every entry's `.id` was undefined.
    """
    client = (PROJECT_ROOT / "frontend/src/api/models.ts").read_text(encoding="utf-8")
    screen = (PROJECT_ROOT / "frontend/src/routes/ModelsScreen.tsx").read_text(encoding="utf-8")
    assert "export interface ModelSearchResult" in client
    assert "ctx_length?: number" in client
    assert "api_base" in client
    assert "model_type" in client
    assert "Set context length manually" in screen
    assert "Custom…" in screen
