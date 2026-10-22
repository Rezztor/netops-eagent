import io
import os
import re
import tempfile
import threading
from typing import Any, Callable

from .files import get_abs_path
from dotenv import load_dotenv as _load_dotenv
from dotenv.parser import parse_stream

KEY_RFC_PASSWORD = "REZZTOR_RFC_PASSWORD"
KEY_ROOT_PASSWORD = "REZZTOR_ROOT_PASSWORD"
KEY_SMTP_PASSWORD = "REZZTOR_SMTP_PASSWORD"
KEY_SOLARWINDS_PASSWORD = "REZZTOR_SOLARWINDS_PASSWORD"
# Not `API_KEY_*` and not in secrets._RUNTIME_CREDENTIAL_KEYS: the agent must never be able to use these as §§secret() aliases.
KEY_AZURE_CLIENT_SECRET = "REZZTOR_AZURE_CLIENT_SECRET"
KEY_AZURE_CLIENT_CERT = "REZZTOR_AZURE_CLIENT_CERT"  # base64 of the PEM private key then the PEM certificate, one line
# The bearer token the application sends the cloud executor for plan, apply and action. Same rule: never `API_KEY_*`.
KEY_CLOUD_JOB_TOKEN = "REZZTOR_CLOUD_JOB_TOKEN"

def load_dotenv():
    # interpolate off: a password that contains `${...}` is that text, not a reference to another variable.
    _load_dotenv(get_dotenv_file_path(), override=True, interpolate=False)


def get_dotenv_file_path():
    return get_abs_path("usr/.env")

def get_dotenv_value(key: str, default: Any = None):
    # load_dotenv()
    return os.getenv(key, default)


def get_or_create_dotenv_value(key: str, create: Callable[[], str]) -> str:
    """The value of `key` in usr/.env, made by `create` and saved only when the file has none.

    The environment holds usr/.env only once load_dotenv() has run, and a process that never ran it -- the
    appliance console's `python -c` in the container -- found nothing, made a new value and wrote it over the
    stored one. The server kept its own until its next start. For the persistent id that start was an update's
    restart: the session cookie was renamed and everybody was signed out, and the licence's installation id moved.
    """
    value = get_dotenv_value(key)
    if not value:
        load_dotenv()
        value = get_dotenv_value(key)
    if not value:
        value = create()
        save_dotenv_value(key, value)
    return value


# Where a value came from. `usr/.env` wins: load_dotenv() above is called with
# override=True, so a value written through the interface replaces one injected
# from the process environment.
ORIGIN_FILE = "file"
ORIGIN_ENVIRONMENT = "environment"
ORIGIN_UNSET = "unset"


def read_dotenv_file_keys() -> set[str]:
    """Keys with a non-empty value in usr/.env.

    Read from the file rather than from os.environ, which cannot distinguish the
    two sources after load_dotenv has merged them.

    Empty entries are excluded deliberately. The settings writer records a line
    for every known provider whether or not a key was supplied, so the file
    normally holds thirty-odd `API_KEY_X=` lines that set nothing -- counting
    those as configured would report every provider as configured.
    """
    path = get_dotenv_file_path()
    if not os.path.isfile(path):
        return set()
    keys: set[str] = set()
    try:
        with open(path, "r") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                name, _, value = stripped.partition("=")
                if value.strip().strip("'\""):
                    keys.add(name.strip())
    except OSError:
        return set()
    return keys


def value_origin(key: str) -> str:
    """Which source supplies `key`, for surfacing in the interface.

    This matters because the two can silently disagree. A key set through the
    interface is written to usr/.env, which lives on the persistent volume and
    overrides the environment -- so rotating an injected variable and redeploying
    leaves the instance still using the old value, with nothing to indicate it.
    """
    file_keys = read_dotenv_file_keys()
    if key in file_keys:
        return ORIGIN_FILE
    # An empty value is not a configured one. load_dotenv(override=True) copies
    # every line of usr/.env into the environment, blank entries included, so
    # testing only for presence reports each of the thirty-odd empty
    # `API_KEY_X=` lines as environment-provided.
    if (os.getenv(key) or "").strip():
        return ORIGIN_ENVIRONMENT
    return ORIGIN_UNSET

_PLAIN = re.compile(r"[A-Za-z0-9_@%+=:,./-]*\Z")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _reads_back(text: str, value: str) -> bool:
    """Whether `KEY=<text>` gives `value` back and leaves the next line alone.

    The line after it is quoted on purpose. The reader takes `\\"` for an
    escaped quote, so a quoted value that ends in a backslash is read on to the
    next quote in the file: alone it happens to parse, and above a quoted line
    it loses itself and that line's key as well.
    """
    read = list(parse_stream(io.StringIO(f'K={text}\nZ="1"\n')))
    return [(b.key, b.value, b.error) for b in read] == [("K", value, False), ("Z", "1", False)]


def _quote(value: str, bare_if_plain: bool = True) -> str:
    """`value` as one line the loader reads back unchanged.

    Written bare, a password with `#` lost everything after it, one with a space or a quote came back different,
    and one with a newline wrote a second line: another key, set by whoever chose the password. Quoted and
    escaped covers those. A value ending in a backslash is the one case the quotes cannot hold (`_reads_back`),
    so it is written bare when that reads back, and refused when neither spelling does.
    """
    value = str(value)
    if bare_if_plain and _PLAIN.match(value):
        return value
    quoted = '"' + "".join(_ESCAPES.get(ch, ch) for ch in value) + '"'
    for text in (quoted, value):
        if _reads_back(text, value):
            return text
    raise ValueError(
        "This value cannot be stored: it ends with a backslash and also holds something that needs "
        "quoting (a space at either end, a `#` after a space, a quote at the start or a line break)."
    )


#: One writer at a time. Every save reads the whole file, changes one line and
#: writes the whole file back, and requests on other threads do the same.
_save_lock = threading.Lock()


def save_dotenv_value(key: str, value: str, reload_env: bool = True):
    if value is None:
        value = ""
    # Before the file is opened: a value that cannot be stored leaves it alone.
    line = f"{key}={_quote(value)}\n"
    dotenv_path = get_dotenv_file_path()
    with _save_lock:
        try:
            with open(dotenv_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        found = False
        for i, existing in enumerate(lines):
            if re.match(rf"^\s*{re.escape(key)}\s*=", existing):
                lines[i] = line
                found = True
        if not found:
            lines.append(f"\n{line}")
        # Replaced whole, never rewritten in place: a reader on another thread
        # sees the old file or the new one, not one cut short. mkstemp makes it
        # 0600, which is right for a file of credentials.
        handle, temporary = tempfile.mkstemp(dir=os.path.dirname(dotenv_path) or ".", prefix=".env.")
        try:
            with os.fdopen(handle, "w") as f:
                f.writelines(lines)
            os.replace(temporary, dotenv_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    if reload_env:
        load_dotenv()
