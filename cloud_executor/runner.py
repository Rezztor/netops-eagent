"""Plan, apply and verify one Azure change with OpenTofu (ADR 0061 to 0065).

Every run happens in a fresh directory on tmpfs that is removed when the run ends, with an environment built from
scratch: the identity's certificate (base64, made in memory), the state backend and the state passphrase, the mirror's
CLI configuration, and nothing inherited. The plan file is made in that directory, read once and removed with it.

**This module has one way to make a change and no way to remove anything.** It runs `init`, `workspace`, `validate`,
`plan`, `show`, `apply` and a final `plan -detailed-exitcode` to verify; a test scans the executor's source for the
words that would remove a resource, and `gate.check_plan` refuses any plan that deletes, replaces or forgets.
The approved artefact is the plan digest (ADR 0062): `apply` plans again, judges again, and applies only if the
digest is the one the engineer approved.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from cloud_executor import adopt, arm, cost, db, gate, keys, store, templates
from executor import secret_env

AZURERM_VERSION = "5.8.0"
AZAPI_VERSION = "2.13.0"
ENV_TOFU = "REZZTOR_CLOUD_TOFU"
ENV_TFRC = "REZZTOR_CLOUD_TFRC"
ENV_RUN_DIR = "REZZTOR_CLOUD_RUN_DIR"
ENV_TIMEOUT = "REZZTOR_CLOUD_JOB_TIMEOUT"
ENV_COMMIT = "REZZTOR_CLOUD_TEMPLATES_COMMIT"
MAX_CODE = 12_000
MAX_OUTPUT = 64 * 1024 * 1024
GUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
GROUP = re.compile(r"[A-Za-z0-9._()-]{1,90}")
CHANGE_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")
EXECUTOR_FILE = "zz_cloud_executor.tf"
STAGES = ("gate", "init", "validate", "plan", "policy", "cost")
_SAFE_ID = re.compile(r"(/[a-z0-9._()-]{1,260}){5,20}")


class Refusal(Exception):
    """The change cannot go on. `stage` is one of `STAGES`; the text is safe to show a person."""

    def __init__(self, stage: str, text: str) -> None:
        super().__init__(text)
        self.stage, self.text = stage, text


def templates_commit() -> str:
    return (os.getenv(ENV_COMMIT) or "unpinned").strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


# ------------------------------------------------------------------------------------------------ the source

class Source:
    """What a request names, resolved: the files, the values, the policy's view of it and the digest's."""

    def __init__(self, files: dict[str, str], tfvars: dict[str, Any] | None, draft: dict[str, Any],
                 digest_source: dict[str, Any], state_key: str, variables: dict[str, Any]) -> None:
        self.files, self.tfvars, self.draft = files, tfvars, draft
        self.digest_source, self.state_key, self.variables = digest_source, state_key, variables

    @property
    def workspace_name(self) -> str:
        """OpenTofu's workspace names cannot hold a slash, so the state row is named by a hash of the readable key."""
        return "w" + hashlib.sha256(self.state_key.encode()).hexdigest()[:40]

    @property
    def code(self) -> str:
        return "\n".join(f"# file: {name}\n{text}" for name, text in sorted(self.files.items()))


def workspace_of(body: Any) -> tuple[str, str]:
    ws = body.get("workspace") if isinstance(body, dict) else None
    sub, group = (ws.get("subscription"), ws.get("resource_group")) if isinstance(ws, dict) else (None, None)
    if not isinstance(sub, str) or not GUID.fullmatch(sub) or not isinstance(group, str) or not GROUP.fullmatch(group):
        raise Refusal("gate", "The workspace needs a subscription GUID and a resource group name.")
    return sub.lower(), group


def resolve(source: Any, sub: str, group: str, rails: dict[str, Any], catalogue: dict[str, Any]) -> Source:
    """The source, or `Refusal`. Its `draft` also names the templates that make resource groups (`makers`) and, for a
    template, the parents it refers to by name and never reads (`parent_ids`), for `judge`."""
    if not isinstance(source, dict) or len({"template", "code"} & set(source)) != 1:
        raise Refusal("gate", "A source is a template with its values, or code.")
    if sub not in {s.lower() for s in rails["subscriptions"]}:
        raise Refusal("gate", f"The subscription {sub} is not one this product may change.")
    if "template" in source:
        entry = catalogue.get(str(source["template"]))
        if entry is None:
            raise Refusal("gate", f"There is no template named {source['template']}.")
        try:
            clean = entry.validate(source.get("variables"), rails["regions"])
        except templates.VariableRejected as error:
            raise Refusal("gate", str(error)) from error
        found = Source(entry.code_files(), entry.tfvars(clean, sub, group),
                       {"template": entry.name, "may_change": entry.may_change, "restarts_existing": entry.restarts_existing,
                        "subscription": sub, "resource_group": group, "target_ids": entry.target_ids(clean),
                        "parent_ids": entry.parent_ids(clean, sub, group)},
                       {"template": entry.name, "commit": templates_commit(), "variables": clean},
                       entry.state_key(clean, sub, group), clean)
    else:
        code = source["code"]
        if not rails["allow_free_code"]:
            raise Refusal("gate", "Free code is switched off in the guard rails.")
        if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE:
            raise Refusal("gate", f"Code is one text of at most {MAX_CODE} characters.")
        found = Source({"main.tf": code}, None,
                       {"template": "free", "may_change": None, "restarts_existing": False,
                        "subscription": sub, "resource_group": group},
                       {"code": code}, f"{sub}/{group}/free/{hashlib.sha256(code.encode()).hexdigest()[:16]}", {})
    found.draft["makers"] = sorted(n for n, e in catalogue.items() if "azurerm_resource_group" in e.types)
    refusals = gate.check_code(found.files)
    if refusals:
        raise Refusal("gate", refusals[0])
    return found


# ------------------------------------------------------------------------------------------------ the run

def _encryption() -> str:
    """`TF_ENCRYPTION`: state and plan files are AES-GCM encrypted under the passphrase, and unencrypted is refused."""
    quoted = json.dumps(keys.passphrase()).replace("${", "$${").replace("%{", "%%{")
    return (f'key_provider "pbkdf2" "p" {{\n  passphrase = {quoted}\n}}\n'
            'method "aes_gcm" "m" {\n  keys = key_provider.pbkdf2.p\n}\n'
            'state {\n  method   = method.aes_gcm.m\n  enforced = true\n}\n'
            'plan {\n  method   = method.aes_gcm.m\n  enforced = true\n}\n')


def _executor_file(sub: str) -> str:
    """The provider and backend blocks. Free code cannot carry any (the gate refuses them); this file is ours alone."""
    return (
        'terraform {\n  required_providers {\n'
        f'    azurerm = {{ source = "hashicorp/azurerm", version = "= {AZURERM_VERSION}" }}\n'
        f'    azapi   = {{ source = "azure/azapi", version = "= {AZAPI_VERSION}" }}\n  }}\n'
        '  backend "pg" {\n    schema_name          = "public"\n    skip_schema_creation = true\n'
        '    skip_table_creation  = true\n    skip_index_creation  = true\n  }\n}\n'
        f'provider "azurerm" {{\n  features {{}}\n  subscription_id                 = "{sub}"\n'
        '  resource_provider_registrations = "none"\n  storage_use_azuread             = true\n}\n'
        f'provider "azapi" {{\n  subscription_id = "{sub}"\n}}\n')


def environment(role: str, work: Path) -> tuple[dict[str, str], list[str]]:
    """The child's whole environment, and the secrets in it (so output can be scrubbed). Nothing is inherited."""
    who = keys.identity(role)
    if not who:
        raise Refusal("init", f"The {role} key has not been made yet. An administrator makes it in Settings.")
    try:
        pfx, state_url = keys.pfx_b64(role), db.url()
        encryption = _encryption()
    except (keys.KeyRefused, RuntimeError) as error:
        raise Refusal("init", str(error)) from error
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work / "tmp"),
        "TF_DATA_DIR": str(work / ".terraform"), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0",
        "TF_CLI_CONFIG_FILE": os.getenv(ENV_TFRC) or "/etc/tofu/cli.tfrc", "TF_ENCRYPTION": encryption,
        "ARM_TENANT_ID": who[0], "ARM_CLIENT_ID": who[1], "ARM_CLIENT_CERTIFICATE": pfx,
        "ARM_USE_CLI": "false", "ARM_USE_MSI": "false", "ARM_USE_OIDC": "false", "ARM_USE_AKS_WORKLOAD_IDENTITY": "false",
        "PG_CONN_STR": state_url,
    }
    return env, [pfx, state_url, secret_env.get(db.ENV_PASSWORD), keys.passphrase()]


def _scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text.strip()[-1500:]


def tofu(args: list[str], work: Path, env: dict[str, str], secrets: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    """Run the one binary, no shell, its own process group (so a timeout takes the providers with it)."""
    process = subprocess.Popen([os.getenv(ENV_TOFU) or "tofu", *args], cwd=work, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = process.communicate(timeout=timeout or _int_env(ENV_TIMEOUT, 1800))
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return 124, "", "The run took too long and was stopped."
    if len(out) > MAX_OUTPUT:
        return 1, "", "OpenTofu's answer was too large to read."
    return process.returncode, out, _scrub(err or out[-1500:], secrets) if process.returncode else err


@contextlib.contextmanager
def workdir(src: Source):
    """A fresh 0700 directory under the run directory (tmpfs in the container), removed when the run ends."""
    root = Path(os.getenv(ENV_RUN_DIR) or "/tmp/cloud-runs")
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    try:
        (work / "tmp").mkdir()
        for name, text in src.files.items():
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if src.tfvars is not None:
            (work / "terraform.tfvars.json").write_text(json.dumps(src.tfvars), encoding="utf-8")
        with open(work / EXECUTOR_FILE, "x", encoding="utf-8") as handle:  # "x": never over a file the source brought
            handle.write(_executor_file(src.draft["subscription"]))
        yield work
    finally:
        shutil.rmtree(work, ignore_errors=True)


def plan_in(work: Path, src: Source, mode: str, env: dict[str, str], secrets: list[str], keep: bool = False) -> dict[str, Any]:
    """init, select the state, validate, plan into `plan.bin` and read it as JSON. Raises `Refusal` at the stage that
    failed. The plan file is removed as soon as it is read unless `keep` (an apply needs it)."""
    steps = (("init", ["init", "-input=false", "-no-color"]),
             ("init", ["workspace", "select", "-or-create", src.workspace_name]),
             ("validate", ["validate", "-no-color"]),
             ("plan", ["plan", "-input=false", "-no-color", "-lock-timeout=60s", "-out=plan.bin",
                       *(["-refresh-only"] if mode == "refresh_only" else [])]))
    for stage, args in steps:
        code, _out, err = tofu(args, work, env, secrets)
        if code:
            raise Refusal(stage, f"{args[0]} failed: {err}")
    code, out, err = tofu(["show", "-json", "-no-color", "plan.bin"], work, env, secrets)
    if not keep:
        (work / "plan.bin").unlink(missing_ok=True)
    try:
        plan = json.loads(out)
    except ValueError:
        plan = None
    if code or not isinstance(plan, dict):
        raise Refusal("plan", f"The plan could not be read: {err}")
    return plan


def existing_ids(sub: str, group: str, extra: list[str]) -> set[str]:
    """What already exists in the resource group (read with the planner's key) plus what the caller says exists."""
    found = {str(i).lower() for i in extra}
    try:
        token = arm.token("planner")
        path: str | None = f"/subscriptions/{sub}/resourcegroups/{group}/resources?api-version=2021-04-01"
        for _ in range(10):
            page = arm.get(token, path) if path else None
            if not page:
                break
            found |= {str(r.get("id")).lower() for r in page.get("value") or [] if r.get("id")}
            nxt = str(page.get("nextLink") or "")
            path = nxt[len(arm.MANAGEMENT):] if nxt.startswith(arm.MANAGEMENT + "/") else None
    except arm.ArmError as error:
        raise Refusal("policy", f"Azure could not be asked what already exists, so nothing is planned: {error}") from error
    return found


def _check_parents(draft: dict[str, Any], rails: dict[str, Any], pending: dict[str, set[str]], groups: set[str]) -> None:
    """Every parent the template names is made by an earlier step of this call, or is in Azure (a GET with the planner's
    key) in a resource group this product may change. The plan does not read the parent, so this is what proves it."""
    existing = {str(g).lower() for g in (rails.get("resource_groups") or {}).get("existing") or []} | groups
    token = ""
    for pid, ptype in draft.get("parent_ids") or []:
        name = pid.rstrip("/").rsplit("/", 1)[-1]
        if pid in pending["ids"]:
            continue
        sub, group = gate._id_scope(pid)  # noqa: SLF001 - the gate's own reading of an ID
        version = adopt.ARM_TYPES.get(ptype, ("", ""))[1]
        if not _SAFE_ID.fullmatch(pid) or not version or sub != draft["subscription"]:
            raise Refusal("policy", f"{name} is not a parent this change may refer to.")
        if group not in existing:
            raise Refusal("policy", f"{name} is in the resource group {group}, which is not one this product may change.")
        try:
            token = token or arm.token("planner")
            found = arm.get(token, f"{pid}?api-version={version}")
        except arm.ArmError as error:
            raise Refusal("policy", f"Azure could not be asked whether {name} exists, so nothing is planned: {error}") from error
        if found is None:
            raise Refusal("policy", f"{name} does not exist, and no earlier step makes it.")


def judge(plan: dict[str, Any], src: Source, mode: str, rails: dict[str, Any], inventory: list[str],
          lookup: Any = None, pending: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """The policy, the digest, the summary and the cost, or `Refusal`. The answer of `/v1/plan` without `ok`.

    `pending` is what earlier steps of one `plan-set` call will make, `{"groups", "ids"}`: it is built by `plan_set` from
    plans this executor ran, never taken from a request. A resource group is in scope when it is in the guard rails, was
    made by this executor (ADR 0074) or is pending."""
    pending = pending or {"groups": set(), "ids": set()}
    sub, group = src.draft["subscription"], src.draft["resource_group"]
    try:
        made = store.made_groups(sub, src.draft.get("makers", []))
    except Exception as error:  # noqa: BLE001 - not knowing what was made is a refusal, never "nothing"
        raise Refusal("policy", f"The executor could not read which resource groups it has made ({type(error).__name__}), "
                                "so nothing is planned.") from error
    groups = made | pending["groups"]
    _check_parents(src.draft, rails, pending, groups)
    has_create = any(gate.change_action(rc) == "create" for rc in gate.managed_changes(plan))
    ids = existing_ids(sub, group, inventory) if has_create or src.draft["restarts_existing"] else {str(i).lower() for i in inventory}
    checks, refusals = gate.check_plan(plan, rails, {**src.draft, "groups_ok": sorted(groups)}, ids)
    if refusals:
        raise Refusal("policy", "\n".join(refusals[:5]))
    summary = gate.summarise(plan)
    if mode == "import" and (summary["add"] or summary["change"]):
        raise Refusal("policy", "An import changes nothing in Azure; this plan would add or change something.")
    estimate = cost.estimate(plan, lookup)
    cost_checks, cost_refusals = cost.policy(estimate, rails, store.committed_usd())
    if cost_refusals:
        raise Refusal("cost", "\n".join(cost_refusals))
    return {"plan_digest": gate.plan_digest(plan, src.digest_source, {"subscription": sub, "resource_group": group}),
            "summary": summary, "checks": checks + cost_checks, "cost": estimate, "drift": gate.drift_lines(plan)}


def plan(body: dict[str, Any], rails: dict[str, Any], catalogue: dict[str, Any], lookup: Any = None) -> dict[str, Any]:
    """`POST /v1/plan`. A refusal is an answer (`ok: false, stage, refusal`), never an exception."""
    started = time.monotonic()
    mode = body.get("mode", "apply")
    try:
        if mode not in ("apply", "refresh_only", "import"):
            raise Refusal("gate", "The mode is apply, refresh_only or import. There is no other.")
        sub, group = workspace_of(body)
        src = resolve(body.get("source"), sub, group, rails, catalogue)
        with workdir(src) as work:
            env, secrets = environment("planner", work)
            result = judge(plan_in(work, src, mode, env, secrets), src, mode, rails, _inventory(body), lookup)
    except Refusal as refusal:
        return {"ok": False, "stage": refusal.stage, "refusal": refusal.text}
    return {"ok": True, **result, "duration_s": round(time.monotonic() - started, 1)}


def _creates(plan: dict[str, Any], sub: str) -> set[str]:
    """The Azure IDs a plan makes or changes (lower case), as far as the plan lets the gate know them."""
    found: set[str] = set()
    for rc in gate.managed_changes(plan):
        if gate.change_action(rc) in ("create", "update"):
            after = (rc.get("change") or {}).get("after") or {}
            found |= set(gate.arm_ids(rc, sub)) | ({after["resource_id"].lower()} if isinstance(after.get("resource_id"), str) else set())
    return found


def plan_set(body: dict[str, Any], rails: dict[str, Any], catalogue: dict[str, Any], lookup: Any = None,
             lock: Any = None) -> dict[str, Any]:
    """`POST /v1/plan-set`: every step planned for real, in order, each against what the earlier steps will make (ADR 0073).

    What an earlier step makes (its group, the IDs it creates) is `pending` for the next, and it is built here from plans
    this executor ran: a request cannot claim a group or a parent is pending. `lock(sub, group)` is a context manager
    held while one step plans (the application passes its per-workspace lock). A refusal is an answer naming the step
    (`step`, 0 for the set); no plan file survives a step."""
    started = time.monotonic()
    lock = lock or (lambda sub, group: contextlib.nullcontext())
    n = 0
    try:
        sub, steps = body.get("subscription"), body.get("steps")
        if not isinstance(sub, str) or not GUID.fullmatch(sub) or sub.lower() not in {s.lower() for s in rails["subscriptions"]}:
            raise Refusal("gate", "The set needs a subscription this product may change.")
        sub = sub.lower()
        if not isinstance(steps, list) or not 2 <= len(steps) <= gate.SET_MAX_STEPS:
            raise Refusal("gate", f"A change set has 2 to {gate.SET_MAX_STEPS} steps. One step is a single change.")
        pending: dict[str, set[str]] = {"groups": set(), "ids": set()}
        keys_seen: dict[str, int] = {}
        done: list[dict[str, Any]] = []
        for n, step in enumerate(steps, 1):
            if not isinstance(step, dict) or set(step) != {"template", "variables", "resource_group"}:
                raise Refusal("gate", "A step is a template, its values and a resource group, and nothing else. "
                                      "Free code is never a step.")
            group = step["resource_group"]
            if not isinstance(group, str) or not GROUP.fullmatch(group):
                raise Refusal("gate", "A step needs a resource group name.")
            src = resolve({"template": step["template"], "variables": step["variables"]}, sub, group, rails, catalogue)
            if src.state_key in keys_seen:
                raise Refusal("gate", f"Step {n} makes the same thing as step {keys_seen[src.state_key]}: one step per thing.")
            keys_seen[src.state_key] = n
            with lock(sub, group), workdir(src) as work:
                env, secrets = environment("planner", work)
                plan = plan_in(work, src, "apply", env, secrets)
                result = judge(plan, src, "apply", rails, [], lookup, pending)
            if not result["summary"]["add"] and not result["summary"]["change"]:
                raise Refusal("policy", "This step changes nothing in Azure.")
            clash = sorted(_creates(plan, sub) & pending["ids"])
            if clash:
                raise Refusal("policy", f"This step makes or changes {clash[0]}, which an earlier step makes: one step per thing.")
            if any(rc.get("type") == "azurerm_resource_group" and gate.change_action(rc) == "create" for rc in gate.managed_changes(plan)):
                pending["groups"].add(group.lower())
            pending["ids"] |= _creates(plan, sub)
            done.append({"n": n, "template": src.draft["template"], "resource_group": group, **result})
        n = 0
        total = round(sum(d["cost"]["monthly_usd"] for d in done), 2)
        count = sum(d["summary"]["add"] + d["summary"]["change"] + d["summary"]["import"] for d in done)
        committed, per, ceiling = store.committed_usd(), float(rails["usd_month_per_change"]), float(rails["usd_month_total"])
        problems = {"Cost of the set": [f"{total:.2f} USD a month is more than the {per:.2f} USD limit for one change."] if total > per else [],
                    "Cost across approved changes": [f"{committed + total:.2f} USD a month across approved changes is more than the {ceiling:.2f} USD limit."]
                    if committed + total > ceiling else [],
                    "Number of resources in the set": [f"{count} resources is more than the limit of {rails['max_resources']}."]
                    if count > int(rails["max_resources"]) else []}
        checks = [{"label": label, "ok": not found, **({"detail": found[0]} if found else {})} for label, found in problems.items()]
        for label, found in problems.items():
            if found:
                raise Refusal("policy" if label.startswith("Number") else "cost", found[0])
    except Refusal as refusal:
        return {"ok": False, "step": n, "stage": refusal.stage, "refusal": refusal.text}
    cost_all = {"monthly_usd": total,
                "items": [{**i, "step": d["n"]} for d in done for i in d["cost"]["items"]],
                "unknown": [f"step {d['n']}: {a}" for d in done for a in d["cost"]["unknown"]],
                "priced_at": done[0]["cost"]["priced_at"]}
    return {"ok": True, "set_digest": gate.set_digest(sub, done),
            "steps": [{k: d[k] for k in ("n", "plan_digest", "summary", "checks", "cost", "drift")} for d in done],
            "cost": cost_all, "checks": checks, "duration_s": round(time.monotonic() - started, 1)}


def _inventory(body: dict[str, Any]) -> list[str]:
    ids = body.get("inventory_ids")
    return [i for i in ids[:20000] if isinstance(i, str) and len(i) < 400] if isinstance(ids, list) else []


# ---------------------------------------------------------------------------------------------- the apply

def _ids_of(state: Any, addresses: set[str]) -> list[str]:
    """The Azure IDs of the resources at `addresses` in `tofu show -json` state output."""
    found: list[str] = []

    def walk(module: dict[str, Any]) -> None:
        for r in module.get("resources") or []:
            rid = (r.get("values") or {}).get("id")
            if r.get("address") in addresses and isinstance(rid, str) and rid.startswith("/"):
                found.append(rid)
        for child in module.get("child_modules") or []:
            walk(child)

    walk(((state or {}).get("values") or {}).get("root_module") or {})
    return sorted(set(found))


def apply(body: dict[str, Any], rails: dict[str, Any], catalogue: dict[str, Any], lookup: Any = None) -> dict[str, Any]:
    """The job body of `/v1/apply`: plan again, judge again, apply if the digest is the approved one, then verify.

    Returns the job fields to store: `status` (applied, verify_failed, failed, superseded), `verified`, `changed_ids`,
    `error`. The request has already been checked for the reference and for a change id used before.
    """
    try:
        sub, group = workspace_of(body)
        src = resolve(body.get("source"), sub, group, rails, catalogue)
        with workdir(src) as work:
            env, secrets = environment("applier", work)
            result = judge(plan_in(work, src, "apply", env, secrets, keep=True), src, "apply", rails, _inventory(body), lookup)
            if result["plan_digest"] != body.get("plan_digest"):
                return {"status": "superseded", "verified": None, "changed_ids": [],
                        "error": "The plan is not the one that was approved. Draft it again."}
            try:
                store.record_apply(workspace=src.state_key, state_name=src.workspace_name, change_id=body["change_id"],
                                   code=src.code, variables=src.variables, plan_digest=body["plan_digest"],
                                   reference=str(body["reference"]).strip().upper())
            except store.AlreadyApplied as error:
                return {"status": "failed", "verified": None, "changed_ids": [], "error": str(error)}
            code, _out, err = tofu(["apply", "-input=false", "-no-color", "-lock-timeout=60s", "plan.bin"], work, env, secrets)
            if code:
                return {"status": "failed", "verified": None, "changed_ids": [], "error": f"apply failed: {err}"}
            made = {r["address"] for r in result["summary"]["resources"]}
            code, out, _err = tofu(["show", "-json", "-no-color"], work, env, secrets)
            try:
                ids = _ids_of(json.loads(out), made) if not code else []
            except ValueError:
                ids = []
            store.commit_usd(body["change_id"], result["cost"]["monthly_usd"])
            verify, _o, err = tofu(["plan", "-detailed-exitcode", "-input=false", "-no-color", "-lock-timeout=60s"],
                                   work, env, secrets)
    except Refusal as refusal:
        return {"status": "failed", "verified": None, "changed_ids": [], "error": f"{refusal.stage}: {refusal.text}"}
    if verify == 0:
        return {"status": "applied", "verified": True, "changed_ids": ids}
    return {"status": "verify_failed", "verified": False, "changed_ids": ids,
            "error": "Azure still differs from the code after the apply." if verify == 2 else f"The check after the apply failed: {err}"}
