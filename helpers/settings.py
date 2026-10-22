import base64
import hashlib
import secrets
import json
import tempfile
import os
import re
from typing import Any, Literal, TypedDict, cast, TypeVar

import models
import pytz  # type: ignore
from psycopg.types.json import Jsonb

from helpers import runtime, defer, git, subagents
from . import files, dotenv
from helpers.print_style import PrintStyle
from helpers.providers import get_providers, FieldOption as ProvidersFO
from helpers.secrets import get_default_secrets_manager
from helpers import dirty_json
from helpers.notification import NotificationManager, NotificationType, NotificationPriority


T = TypeVar('T')

def get_default_value(name: str, value: T) -> T:
    """
    Load setting value from .env with REZZTOR_SET_ prefix, falling back to default.

    Args:
        name: Setting name (will be prefixed with REZZTOR_SET_)
        value: Default value to use if env var not set

    Returns:
        Environment variable value (type-normalized) or default value
    """
    env_value = dotenv.get_dotenv_value(f"REZZTOR_SET_{name}", dotenv.get_dotenv_value(f"REZZTOR_SET_{name.upper()}", None))

    if env_value is None:
        return value

    # Normalize type to match value param type
    try:
        if isinstance(value, bool):
            return env_value.strip().lower() in ('true', '1', 'yes', 'on')  # type: ignore
        elif isinstance(value, dict):
            return json.loads(env_value.strip())  # type: ignore
        elif isinstance(value, str):
            return str(env_value).strip()  # type: ignore
        else:
            return type(value)(env_value.strip())  # type: ignore
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        PrintStyle(background_color="yellow", font_color="black").print(
            f"Warning: Invalid value for REZZTOR_SET_{name}='{env_value}': {e}. Using default: {value}"
        )
        return value

class Settings(TypedDict):
    version: str

    agent_profile: str
    agent_knowledge_subdir: str
    max_consecutive_unusable_responses: int
    timezone: str
    time_format: str
    ui_control_visibility: dict[str, dict[str, bool]]

    workdir_path: str
    workdir_show: bool
    workdir_max_depth: int
    workdir_max_files: int
    workdir_max_folders: int
    workdir_max_lines: int
    workdir_gitignore: str
    file_browser_remember_last_directory: bool

    api_keys: dict[str, str]

    root_password: str

    rfc_auto_docker: bool
    rfc_url: str
    rfc_password: str
    rfc_port_http: int

    websocket_server_restart_enabled: bool
    uvicorn_access_logs_enabled: bool

    mcp_servers: str
    mcp_client_init_timeout: int
    mcp_client_tool_timeout: int
    mcp_server_enabled: bool
    mcp_server_token: str

    a2a_server_enabled: bool

    variables: str
    secrets: str

    # LiteLLM global kwargs applied to all model calls
    litellm_global_kwargs: dict[str, Any]

    chat_inherit_project: bool

    # Outbound mail. The platform sends only to addresses on its own accounts
    # (helpers/mailer.py), so this is an internal notification channel rather
    # than a general mail client.
    smtp_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    smtp_from_address: str
    smtp_from_name: str
    smtp_base_url: str

    # An administrator's own switch, separate from smtp_enabled. Turning SMTP
    # off already stops every reset link from sending; this stops the
    # self-service *flow* specifically, for everyone except administrators, so
    # a deployment can require reset requests to go through an administrator
    # without also losing the ability to send other mail.
    password_reset_enabled: bool

    # An appliance's daily question to Rezztor's update service (ADR 0091), asked
    # by the host through the update spool. Off for an air-gapped or strict site;
    # "Check now" and a manual upload keep working either way.
    update_daily_check: bool


class PartialSettings(Settings, total=False):
    pass


class FieldOption(TypedDict):
    value: str
    label: str

class SettingsField(TypedDict, total=False):
    id: str
    title: str
    description: str
    type: Literal[
        "text",
        "number",
        "select",
        "range",
        "textarea",
        "password",
        "switch",
        "button",
        "html",
    ]
    value: Any
    min: float
    max: float
    step: float
    hidden: bool
    options: list[FieldOption]
    style: str


class SettingsSection(TypedDict, total=False):
    id: str
    title: str
    description: str
    fields: list[SettingsField]
    tab: str  # Indicates which tab this section belongs to

class ModelProvider(ProvidersFO):
    pass

class SettingsOutputAdditional(TypedDict):
    chat_providers: list[ModelProvider]
    embedding_providers: list[ModelProvider]
    agent_subdirs: list[FieldOption]
    knowledge_subdirs: list[FieldOption]
    timezones: list[FieldOption]
    resolved_timezone: str
    is_dockerized: bool
    runtime_settings: dict[str, Any]
    # provider -> "file" | "environment" | "unset"; see dotenv.value_origin
    api_key_origins: dict[str, str]


class SettingsOutput(TypedDict):
    settings: Settings
    additional: SettingsOutputAdditional


PASSWORD_PLACEHOLDER = "****PSWD****"
API_KEY_PLACEHOLDER = "************"
TIMEZONE_AUTO = "auto"
TIME_FORMAT_12H = "12h"
TIME_FORMAT_24H = "24h"
UI_CONTROL_VISIBILITY_DEFAULTS = {
    "projectSelector": {"mobile": True, "desktop": True},
    "time": {"mobile": False, "desktop": True},
    "connectionStatus": {"mobile": True, "desktop": True},
    "rightCanvasRail": {"mobile": True, "desktop": True},
}

KEY_MCP_TOKEN_SALT = "REZZTOR_MCP_TOKEN_SALT"

#: Kept as the name of the thing, not of a path. Settings live in the database
#: now; nothing opens this.
SETTINGS_FILE = "settings"
_settings: Settings | None = None
_runtime_settings_snapshot: Settings | None = None

OptionT = TypeVar("OptionT", bound=FieldOption)

def _ensure_option_present(options: list[OptionT] | None, current_value: str | None) -> list[OptionT]:
    """
    Ensure the currently selected value exists in a dropdown options list.
    If missing, inserts it at the front as {value: current_value, label: current_value}.
    """
    opts = list(options or [])
    if not current_value:
        return opts
    for o in opts:
        if o.get("value") == current_value:
            return opts
    opts.insert(0, cast(OptionT, {"value": current_value, "label": current_value}))
    return opts


def _is_valid_timezone(value: str) -> bool:
    try:
        pytz.timezone(value)
        return True
    except pytz.exceptions.UnknownTimeZoneError:
        return False


def _normalize_timezone_setting(value: Any, default: str = TIMEZONE_AUTO) -> str:
    timezone = str(value or "").strip()
    if timezone.lower() == TIMEZONE_AUTO:
        return TIMEZONE_AUTO
    if _is_valid_timezone(timezone):
        return timezone
    return default if default == TIMEZONE_AUTO or _is_valid_timezone(default) else TIMEZONE_AUTO


def _normalize_time_format(value: Any, default: str = TIME_FORMAT_12H) -> str:
    time_format = str(value or "").strip().lower()
    if time_format in {TIME_FORMAT_12H, TIME_FORMAT_24H}:
        return time_format
    return default if default in {TIME_FORMAT_12H, TIME_FORMAT_24H} else TIME_FORMAT_12H


def _normalize_ui_control_visibility(value: Any) -> dict[str, dict[str, bool]]:
    submitted = value if isinstance(value, dict) else {}
    normalized = {}
    for control, devices in UI_CONTROL_VISIBILITY_DEFAULTS.items():
        submitted_devices = submitted.get(control, {})
        if not isinstance(submitted_devices, dict):
            submitted_devices = {}
        normalized[control] = {
            device: submitted_devices.get(device)
            if isinstance(submitted_devices.get(device), bool)
            else default
            for device, default in devices.items()
        }
    return normalized


def _resolve_runtime_timezone(setting_value: str, browser_timezone: str | None = None) -> str:
    if setting_value == TIMEZONE_AUTO:
        candidate = str(browser_timezone or "").strip()
        if _is_valid_timezone(candidate):
            return candidate
        try:
            from helpers.localization import Localization

            return Localization.get().get_timezone()
        except Exception:
            return "UTC"
    return _normalize_timezone_setting(setting_value, default="UTC")


def apply_browser_timezone(browser_timezone: str | None) -> None:
    """What the timezone a request carries may do to the instance's clock.

    With the setting on `auto` an administrator's browser is followed, as when
    settings are saved. With a zone an administrator fixed, it changes nothing.
    Every poll and every scheduler call carries one, and each used to set it on
    the process directly, so the fixed zone held only until the next page asked
    for anything.

    The clock is one for the instance, and the hour a scheduled task runs at
    hangs on it. So on `auto` only an account that may change settings moves
    it: followed from every account, it went back and forth between the zones
    of whoever asked last, and each move rewrote two lines of usr/.env.
    """
    from helpers import rbac, user_scope, users
    from helpers.localization import Localization

    current = _settings or get_settings()
    setting = str(current.get("timezone") or TIMEZONE_AUTO)
    # Asked only when the request would move the clock, and of the account behind it, which a socket has as well.
    if (
        setting == TIMEZONE_AUTO
        and str(browser_timezone or "").strip() != Localization.get().get_timezone()
        and not users.holds_permission(user_scope.get_current_user_id(), rbac.PERM_SETTINGS_MANAGE)
    ):
        return
    Localization.get().set_timezone(_resolve_runtime_timezone(setting, browser_timezone))


def _timezone_options() -> list[FieldOption]:
    return [{"value": timezone, "label": timezone} for timezone in pytz.common_timezones]


def convert_out(settings: Settings) -> SettingsOutput:
    out = SettingsOutput(
        settings = settings.copy(),
        additional = SettingsOutputAdditional(
            chat_providers=get_providers("chat"),
            embedding_providers=get_providers("embedding"),
            is_dockerized=runtime.is_dockerized(),
            agent_subdirs=[
                {"value": key, "label": item.title or key}
                for key, item in sorted(
                    subagents.get_available_agents_dict(None).items()
                )
                if key != "default"
            ],
            knowledge_subdirs=[{"value": subdir, "label": subdir}
                for subdir in files.get_subdirectories("knowledge", exclude="default")],
            timezones=_timezone_options(),
            resolved_timezone="UTC",
            runtime_settings={},
            api_key_origins={},
        ),
    )

    # ensure dropdown options include currently selected values
    additional = out["additional"]
    current = out["settings"]

    default_settings = get_default_settings()
    runtime_settings = _runtime_settings_snapshot or settings
    additional["runtime_settings"] = {
        "uvicorn_access_logs_enabled": bool(
            runtime_settings.get(
                "uvicorn_access_logs_enabled",
                default_settings["uvicorn_access_logs_enabled"],
            )
        ),
    }

    current_profile = current.get("agent_profile")
    if current_profile and current_profile != "default" and not any(
        option["value"] == current_profile
        for option in additional["agent_subdirs"]
    ):
        additional["agent_subdirs"].append(
            {"value": current_profile, "label": f"{current_profile} (unavailable)"}
        )
    additional["knowledge_subdirs"] = _ensure_option_present(additional.get("knowledge_subdirs"), current.get("agent_knowledge_subdir"))
    if current.get("timezone") != TIMEZONE_AUTO:
        additional["timezones"] = _ensure_option_present(additional.get("timezones"), current.get("timezone"))
    additional["resolved_timezone"] = _resolve_runtime_timezone(current.get("timezone", TIMEZONE_AUTO))

    # masked api keys
    providers = get_providers("chat") + get_providers("embedding")
    for provider in providers:
        provider_name = provider["value"]
        api_key = settings["api_keys"].get(provider_name, models.get_api_key(provider_name))
        settings["api_keys"][provider_name] = API_KEY_PLACEHOLDER if api_key and api_key != "None" else ""

    # load auth from dotenv
    out["settings"]["rfc_password"] = (
        PASSWORD_PLACEHOLDER if dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD) else ""
    )
    out["settings"]["root_password"] = (
        PASSWORD_PLACEHOLDER if dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD) else ""
    )
    out["settings"]["smtp_password"] = (
        PASSWORD_PLACEHOLDER if dotenv.get_dotenv_value(dotenv.KEY_SMTP_PASSWORD) else ""
    )

    #secrets
    secrets_manager = get_default_secrets_manager()
    try:
        out["settings"]["secrets"] = secrets_manager.get_masked_secrets()
    except Exception:
        out["settings"]["secrets"] = ""

    # Where each key actually comes from. Presence alone is not enough to
    # administer these: a key set through the interface is written to usr/.env,
    # which overrides the process environment, so an instance can keep using a
    # stale key after the injected variable has been rotated with nothing to say
    # so. Reported per provider rather than inferred by the frontend.
    additional["api_key_origins"] = {
        provider["value"]: dotenv.value_origin(f"API_KEY_{provider['value'].upper()}")
        for provider in providers
    }

    # mask API keys before sending to frontend
    if isinstance(out["settings"].get("api_keys"), dict):
        for provider, value in list(out["settings"]["api_keys"].items()):
            if value:
                out["settings"]["api_keys"][provider] = API_KEY_PLACEHOLDER

    # normalize certain fields
    for key, value in list(out["settings"].items()):
        # convert kwargs dicts to .env format
        if (key.endswith("_kwargs")) and isinstance(value, dict):
            out["settings"][key] = _dict_to_env(value)
    return out

def _get_api_key_field(settings: Settings, provider: str, title: str) -> SettingsField:
    key = settings["api_keys"].get(provider, models.get_api_key(provider))
    # For API keys, use simple asterisk placeholder for existing keys
    return {
        "id": f"api_key_{provider}",
        "title": title,
        "type": "text",
        "value": (API_KEY_PLACEHOLDER if key and key != "None" else ""),
    }


def convert_in(settings: Settings) -> Settings:
    current = get_settings()

    for key, value in settings.items():
        # Special handling for *_kwargs (stored as .env text)
        if (key.endswith("_kwargs")) and isinstance(value, str):
            current[key] = _env_to_dict(value)
            continue

        current[key] = value
    return current


def get_settings() -> Settings:
    global _settings
    if not _settings:
        _settings = _read_settings_file()
    if not _settings:
        _settings = get_default_settings()
    norm = normalize_settings(_settings)
    _load_sensitive_settings(norm)
    return norm


def reload_settings() -> Settings:
    global _settings
    _settings = None
    return get_settings()


def set_runtime_settings_snapshot(settings: Settings) -> None:
    global _runtime_settings_snapshot
    _runtime_settings_snapshot = settings.copy()


def set_settings(settings: Settings, apply: bool = True, browser_timezone: str | None = None):
    global _settings
    previous = _settings
    _settings = normalize_settings(settings)
    _write_settings_file(_settings)
    if apply:
        _apply_settings(previous, browser_timezone)
    return reload_settings()


def set_settings_delta(delta: dict, apply: bool = True):
    """Merge a partial change into the stored settings.

    Read, merge and write happen in one transaction with the row locked. The
    file version did all three with nothing held, so two administrators saving
    different panels at the same moment each kept their own change and silently
    lost the other's -- the settings screen sends whole sections, so "lost the
    other's" meant a whole panel reverting.
    """
    global _settings
    from helpers import db

    with db.transaction(None) as conn:
        row = conn.execute(
            "SELECT document FROM settings WHERE id FOR UPDATE"
        ).fetchone()
        current = normalize_settings(row[0]) if row else get_default_settings()
        # The stored document holds no passwords and no secrets -- they live in
        # usr/.env and usr/secrets.env -- so it reads them as empty, and saving
        # that wrote the empty values over the real ones. Applying the MCP
        # servers panel cleared the SMTP, RFC and root passwords and every
        # stored secret. Loaded here so a field the delta does not mention is
        # saved as what it already is.
        _load_sensitive_settings(current)
        new = normalize_settings({**current, **delta})  # type: ignore
        # Written on the locked connection: written after the block, the lock
        # was gone before the write and a second save merged into the stale row.
        _write_settings_file(new, conn)

    # Applied after the commit, so whatever the apply reads sees the new row.
    previous, _settings = _settings, new
    if apply:
        _apply_settings(previous)
    return reload_settings()


def merge_settings(original: Settings, delta: dict) -> Settings:
    merged = original.copy()
    merged.update(delta)
    return merged


def normalize_settings(settings: Settings) -> Settings:
    copy = settings.copy()
    default = get_default_settings()

    # adjust settings values to match current version if needed
    if "version" not in copy or copy["version"] != default["version"]:
        _adjust_to_version(copy, default)
        copy["version"] = default["version"]  # sync version

    # remove keys that are not in default
    keys_to_remove = [key for key in copy if key not in default]
    for key in keys_to_remove:
        del copy[key]

    # add missing keys and normalize types
    for key, value in default.items():
        if key not in copy:
            copy[key] = value
        else:
            try:
                copy[key] = type(value)(copy[key])  # type: ignore
                if isinstance(copy[key], str):
                    copy[key] = copy[key].strip()  # strip strings
            except (ValueError, TypeError):
                copy[key] = value  # make default instead

    if copy["agent_profile"] == "default":
        copy["agent_profile"] = "primary"

    # mcp server token is set automatically
    copy["mcp_server_token"] = create_auth_token()
    copy["max_consecutive_unusable_responses"] = max(
        1, copy["max_consecutive_unusable_responses"]
    )
    copy["timezone"] = _normalize_timezone_setting(copy.get("timezone"), default["timezone"])
    copy["time_format"] = _normalize_time_format(copy.get("time_format"), default["time_format"])
    copy["ui_control_visibility"] = _normalize_ui_control_visibility(copy.get("ui_control_visibility"))

    return copy


def _adjust_to_version(settings: Settings, default: Settings):
    # the default prompt subfolder for the root agent is `primary`
    if "version" not in settings or settings["version"].startswith("v0.8"):
        if "agent_profile" not in settings or settings["agent_profile"] == "default":
            settings["agent_profile"] = "primary"



def _load_sensitive_settings(settings: Settings):
    # load api keys from .env
    providers = get_providers("chat") + get_providers("embedding")
    for provider in providers:
        provider_name = provider["value"]
        api_key = settings["api_keys"].get(provider_name) or models.get_api_key(provider_name)
        if api_key and api_key != "None":
            settings["api_keys"][provider_name] = api_key

    # load auth fields from .env
    settings["rfc_password"] = dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD) or ""
    settings["root_password"] = dotenv.get_dotenv_value(dotenv.KEY_ROOT_PASSWORD) or ""
    settings["smtp_password"] = dotenv.get_dotenv_value(dotenv.KEY_SMTP_PASSWORD) or ""

    # load secrets raw content
    secrets_manager = get_default_secrets_manager()
    try:
        settings["secrets"] = secrets_manager.read_secrets_raw()
    except Exception:
        settings["secrets"] = ""


def _read_settings_file() -> Settings | None:
    """The stored settings document, or None on a fresh instance.

    Still named for the file it used to be, because sixty-odd call sites read
    better with a familiar name than with an accurate one. What it reads is the
    single row of the settings table.
    """
    from helpers import db

    with db.transaction(None) as conn:
        row = conn.execute("SELECT document FROM settings WHERE id").fetchone()
    if not row:
        return None
    return normalize_settings(row[0])


def _write_settings_file(settings: Settings, conn=None):
    """Store the document; on `conn` when given, so a caller holding the row lock writes under it."""
    from helpers import db

    settings = settings.copy()
    # The secret half does not live here. Provider API keys and the various
    # passwords go to usr/.env, which stays a file because the database's own
    # password cannot live inside the database.
    _write_sensitive_settings(settings)
    _remove_sensitive_settings(settings)

    statement = """
        INSERT INTO settings (id, document, updated_at)
        VALUES (TRUE, %s, now())
        ON CONFLICT (id) DO UPDATE SET document = EXCLUDED.document,
                                       updated_at = now()
    """
    if conn is not None:
        conn.execute(statement, (Jsonb(settings),))
        return
    with db.transaction(None) as conn:
        conn.execute(statement, (Jsonb(settings),))


def _write_json_atomic(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _remove_sensitive_settings(settings: Settings):
    settings["api_keys"] = {}
    settings["rfc_password"] = ""
    settings["root_password"] = ""
    settings["smtp_password"] = ""
    settings["mcp_server_token"] = ""
    settings["secrets"] = ""


def _write_sensitive_settings(settings: Settings):
    for key, val in settings["api_keys"].items():
        if val != API_KEY_PLACEHOLDER:
            dotenv.save_dotenv_value(f"API_KEY_{key.upper()}", val)

    if settings["rfc_password"] != PASSWORD_PLACEHOLDER:
        dotenv.save_dotenv_value(dotenv.KEY_RFC_PASSWORD, settings["rfc_password"])
    if settings["smtp_password"] != PASSWORD_PLACEHOLDER:
        dotenv.save_dotenv_value(dotenv.KEY_SMTP_PASSWORD, settings["smtp_password"])
    if settings["root_password"] != PASSWORD_PLACEHOLDER:
        if runtime.is_dockerized():
            dotenv.save_dotenv_value(dotenv.KEY_ROOT_PASSWORD, settings["root_password"])
            # Recorded, not applied. Setting the container's own root password
            # needs chpasswd and therefore root, which this process no longer
            # has -- and nothing reads it: code execution moved to a runner pod
            # with its own credentials (ADR 0019, ADR 0020). The value above is
            # still stored so an existing deployment reading it is not broken.

    # Handle secrets separately - merge with existing preserving comments/order and support deletions
    secrets_manager = get_default_secrets_manager()
    submitted_content = settings["secrets"]
    secrets_manager.save_secrets_with_merge(submitted_content)



def get_default_settings() -> Settings:
    gitignore = files.read_file(files.get_abs_path("conf/workdir.gitignore"))
    return Settings(
        version=_get_version(),
        api_keys={},
        root_password="",
        agent_profile=get_default_value("agent_profile", "primary"),
        agent_knowledge_subdir=get_default_value("agent_knowledge_subdir", "custom"),
        max_consecutive_unusable_responses=get_default_value(
            "max_consecutive_unusable_responses", 5
        ),
        timezone=_normalize_timezone_setting(get_default_value("timezone", TIMEZONE_AUTO)),
        time_format=_normalize_time_format(get_default_value("time_format", TIME_FORMAT_12H)),
        ui_control_visibility=_normalize_ui_control_visibility(
            get_default_value("ui_control_visibility", UI_CONTROL_VISIBILITY_DEFAULTS)
        ),
        workdir_path=get_default_value("workdir_path", files.get_abs_path_dockerized("usr/workdir")),
        workdir_show=get_default_value("workdir_show", True),
        workdir_max_depth=get_default_value("workdir_max_depth", 5),
        workdir_max_files=get_default_value("workdir_max_files", 20),
        workdir_max_folders=get_default_value("workdir_max_folders", 20),
        workdir_max_lines=get_default_value("workdir_max_lines", 250),
        workdir_gitignore=get_default_value("workdir_gitignore", gitignore),
        file_browser_remember_last_directory=get_default_value(
            "file_browser_remember_last_directory",
            True,
        ),
        rfc_auto_docker=get_default_value("rfc_auto_docker", True),
        rfc_url=get_default_value("rfc_url", "localhost"),
        rfc_password="",
        rfc_port_http=get_default_value("rfc_port_http", 55080),
        websocket_server_restart_enabled=get_default_value("websocket_server_restart_enabled", True),
        # On by default. Without it a deployment has no record of request rates,
        # status codes or which endpoint returned the 500 someone is asking about --
        # and the audit trail deliberately records decisions, not traffic.
        # helpers/server_startup.py strips the query string from every line, so
        # turning this on cannot put a password-reset token or a user's file paths
        # into the stream ADR 0005 ships off-box.
        uvicorn_access_logs_enabled=get_default_value("uvicorn_access_logs_enabled", True),
        mcp_servers=get_default_value("mcp_servers", '{\n    "mcpServers": {}\n}'),
        mcp_client_init_timeout=get_default_value("mcp_client_init_timeout", 10),
        mcp_client_tool_timeout=get_default_value("mcp_client_tool_timeout", 120),
        mcp_server_enabled=get_default_value("mcp_server_enabled", False),
        mcp_server_token=create_auth_token(),
        a2a_server_enabled=get_default_value("a2a_server_enabled", False),
        variables="",
        secrets="",
        litellm_global_kwargs=get_default_value("litellm_global_kwargs", {}),
        chat_inherit_project=get_default_value("chat_inherit_project", True),
        smtp_enabled=get_default_value("smtp_enabled", False),
        smtp_host=get_default_value("smtp_host", ""),
        smtp_port=get_default_value("smtp_port", 587),
        smtp_security=get_default_value("smtp_security", "starttls"),
        smtp_username=get_default_value("smtp_username", ""),
        smtp_password="",
        smtp_from_address=get_default_value("smtp_from_address", ""),
        smtp_from_name=get_default_value("smtp_from_name", "Rezztor EAgent"),
        smtp_base_url=get_default_value("smtp_base_url", ""),
        password_reset_enabled=get_default_value("password_reset_enabled", True),
        update_daily_check=get_default_value("update_daily_check", True),
    )


def _apply_timezone_setting(previous: Settings | None, browser_timezone: str | None = None) -> None:
    if not _settings:
        return

    from helpers.localization import Localization

    localization = Localization.get()
    previous_timezone = localization.get_timezone()
    target_timezone = _resolve_runtime_timezone(_settings["timezone"], browser_timezone)
    if (
        previous
        and _settings["timezone"] == previous.get("timezone")
        and _settings["timezone"] != TIMEZONE_AUTO
        and previous_timezone == target_timezone
    ):
        return

    localization.set_timezone(target_timezone)


def _apply_settings(previous: Settings | None, browser_timezone: str | None = None):
    global _settings
    if _settings:
        _apply_timezone_setting(previous, browser_timezone)

        from agent import Agent, AgentContext
        from initialize import initialize_agent

        for ctx in AgentContext.all():
            profile = str(
                getattr(ctx.config, "profile", "") or _settings["agent_profile"]
            )
            ctx.config = initialize_agent(override_settings={"agent_profile": profile})
            agent = ctx.root_agent
            while agent:
                agent_profile = str(
                    getattr(getattr(agent, "config", None), "profile", "") or profile
                )
                agent.config = (
                    ctx.config
                    if agent is ctx.root_agent and agent_profile == profile
                    else initialize_agent(
                        override_settings={"agent_profile": agent_profile}
                    )
                )
                agent = agent.get_data(Agent.DATA_NAME_SUBORDINATE)

        # update mcp settings if necessary
        if not previous or _settings["mcp_servers"] != previous["mcp_servers"]:
            from helpers.mcp_handler import MCPConfig

            async def update_mcp_settings(mcp_servers: str):
                PrintStyle(
                    background_color="black", font_color="white", padding=True
                ).print("Updating MCP config...")
                NotificationManager.send_notification(
                    type=NotificationType.INFO,
                    priority=NotificationPriority.NORMAL,
                    message="Updating MCP settings...",
                    display_time=999,
                    group="settings-mcp"
                )

                mcp_config = MCPConfig.get_instance()
                try:
                    MCPConfig.update(mcp_servers)
                except Exception as e:
                    
                    NotificationManager.send_notification(
                        type=NotificationType.ERROR,
                        priority=NotificationPriority.HIGH,
                        message="Failed to update MCP settings",
                        detail=str(e),                        
                    )
                    (
                        PrintStyle(
                            background_color="red", font_color="black", padding=True
                        ).print("Failed to update MCP settings")
                    )
                    (
                        PrintStyle(
                            background_color="black", font_color="red", padding=True
                        ).print(f"{e}")
                    )

                PrintStyle(
                    background_color="#6734C3", font_color="white", padding=True
                ).print("Parsed MCP config:")
                (
                    PrintStyle(
                        background_color="#334455", font_color="white", padding=False
                    # Names only. The parsed document carries each server's
                    # headers and env -- bearer tokens -- and this is the log.
                    ).print(", ".join(str(getattr(s, "name", "")) for s in getattr(mcp_config, "servers", [])))
                )
                NotificationManager.send_notification(
                    type=NotificationType.INFO,
                    priority=NotificationPriority.NORMAL,
                    message="Finished updating MCP settings.",
                    group="settings-mcp"
                )

            task2 = defer.DeferredTask().start_task(
                update_mcp_settings, _settings["mcp_servers"]
            )  # TODO overkill, replace with background task

        # update token in mcp server
        current_token = (
            create_auth_token()
        )  # TODO - ugly, token in settings is generated from dotenv and does not always correspond
        if not previous or current_token != previous["mcp_server_token"]:

            async def update_mcp_token(token: str):
                from helpers.mcp_server import DynamicMcpProxy

                DynamicMcpProxy.get_instance().reconfigure(token=token)

            task3 = defer.DeferredTask().start_task(
                update_mcp_token, current_token
            )  # TODO overkill, replace with background task

        # update token in a2a server
        if not previous or current_token != previous["mcp_server_token"]:

            async def update_a2a_token(token: str):
                from helpers.fasta2a_server import DynamicA2AProxy

                DynamicA2AProxy.get_instance().reconfigure(token=token)

            task4 = defer.DeferredTask().start_task(
                update_a2a_token, current_token
            )  # TODO overkill, replace with background task


def _env_to_dict(data: str):
    result = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if '=' not in line:
            continue
            
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        
        # If quoted, treat as string
        if value.startswith('"') and value.endswith('"'):
            result[key] = value[1:-1].replace('\\"', '"')  # Unescape quotes
        elif value.startswith("'") and value.endswith("'"):
            result[key] = value[1:-1].replace("\\'", "'")  # Unescape quotes
        else:
            # Not quoted, try JSON parse
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
    
    return result


def _dict_to_env(data_dict):
    lines = []
    for key, value in data_dict.items():
        if isinstance(value, str):
            # Quote strings and escape internal quotes
            escaped_value = value.replace('"', '\\"')
            lines.append(f'{key}="{escaped_value}"')
        elif isinstance(value, (dict, list, bool)) or value is None:
            # Serialize as unquoted JSON
            lines.append(f'{key}={json.dumps(value, separators=(",", ":"))}')
        else:
            # Numbers and other types as unquoted strings
            lines.append(f'{key}={value}')
    
    return "\n".join(lines)


def get_runtime_config(set: Settings):
    # SSH config is now managed by the code_execution plugin.
    # This function is kept for backward compatibility but returns an empty dict.
    return {}


def create_auth_token() -> str:
    """Derive the MCP server token.

    Previously mixed in the shared AUTH_LOGIN/AUTH_PASSWORD pair, which no longer
    exists now that access is per-account. The token is derived from the
    persistent runtime id and a stored salt so that it is stable across restarts
    but not guessable from the runtime id alone.
    """
    runtime_id = runtime.get_persistent_id()
    salt = dotenv.get_or_create_dotenv_value(KEY_MCP_TOKEN_SALT, lambda: secrets.token_urlsafe(24))
    hash_bytes = hashlib.sha256(f"{runtime_id}:{salt}".encode()).digest()
    b64_token = base64.urlsafe_b64encode(hash_bytes).decode().replace("=", "")
    return b64_token[:16]


def _get_version():
    return git.get_version()
