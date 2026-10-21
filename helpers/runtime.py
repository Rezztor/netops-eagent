import argparse
import inspect
import secrets
from typing import TypeVar, Callable, Awaitable, Union, overload, cast
from helpers import dotenv
import asyncio
import threading
import queue
import sys
import nest_asyncio

nest_asyncio.apply()

T = TypeVar("T")
R = TypeVar("R")

parser = argparse.ArgumentParser()
args = {}
dockerman = None
runtime_id = None


def initialize():
    global args
    if args:
        return
    parser.add_argument("--port", type=int, default=None, help="Web UI port")
    parser.add_argument("--host", type=str, default=None, help="Web UI host")
    parser.add_argument(
        "--development", type=bool, default=False, help="Development mode"
    )

    known, unknown = parser.parse_known_args()
    args = vars(known)
    for arg in unknown:
        if "=" in arg:
            key, value = arg.split("=", 1)
            key = key.lstrip("-")
            args[key] = value


def get_arg(name: str):
    global args
    return args.get(name, None)


def has_arg(name: str):
    global args
    return name in args


def is_dockerized() -> bool:
    return bool(get_arg("dockerized"))


def is_development() -> bool:
    return not is_dockerized()


def get_local_url():
    if is_dockerized():
        return "host.docker.internal"
    return "127.0.0.1"


def get_runtime_id() -> str:
    global runtime_id
    if not runtime_id:
        runtime_id = secrets.token_hex(8)
    return runtime_id


def get_persistent_id() -> str:
    """An id that survives a restart, stored in usr/.env.

    Distinct from get_runtime_id(), which is per-process. Anything that has to
    stay recognisable across a restart -- notably the session cookie name --
    must use this one, or a restart silently invalidates it.
    """
    id = dotenv.get_dotenv_value("REZZTOR_PERSISTENT_RUNTIME_ID")
    if not id:
        id = secrets.token_hex(16)
        dotenv.save_dotenv_value("REZZTOR_PERSISTENT_RUNTIME_ID", id)
    return id


def get_session_cookie_id() -> str:
    """The id that names the session cookie.

    Two instances sharing a hostname -- localhost:5000 and localhost:5001 in
    development -- would otherwise collide on one cookie and sign each other
    out, which is why the name is qualified at all. But qualifying it with the
    *per-process* id meant every restart renamed the cookie and signed out every
    user, whatever FLASK_SECRET_KEY did. That is every restart of the container.

    The persistent id keeps both properties: it lives in usr/.env, so it is
    stable across restarts and still differs between installations with separate
    data directories.

    Falls back to the per-process id if usr/ cannot be written. That reintroduces
    the sign-out, which is bad, but it is better than refusing to serve -- and
    the warning says which one happened.
    """
    try:
        return get_persistent_id()
    except Exception as exc:  # noqa: BLE001 - must not prevent startup
        from helpers.print_style import PrintStyle

        PrintStyle.error(
            f"Could not read or create a persistent runtime id ({exc}); falling back "
            "to a per-process id. Sessions will not survive a restart."
        )
        return get_runtime_id()


@overload
async def call_development_function(
    func: Callable[..., Awaitable[T]], *args, **kwargs
) -> T: ...


@overload
async def call_development_function(func: Callable[..., T], *args, **kwargs) -> T: ...


async def call_development_function(
    func: Union[Callable[..., T], Callable[..., Awaitable[T]]], *args, **kwargs
) -> T:
    """Run a function, awaiting it when it is a coroutine function.

    The name is from when a process outside the container forwarded the call to a paired instance inside one
    (RFC). That link was removed on 2026-10-11: its endpoint ran any module's function for whoever held a
    pre-shared key, and had refused every call since the endpoint was put behind a session. The many callers
    keep the name; the function now only runs what it is given, here.
    """
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    return func(*args, **kwargs)  # type: ignore


def call_development_function_sync(
    func: Union[Callable[..., T], Callable[..., Awaitable[T]]], *args, **kwargs
) -> T:
    # run async function in sync manner
    result_queue = queue.Queue()

    def run_in_thread():
        result = asyncio.run(call_development_function(func, *args, **kwargs))
        result_queue.put(result)

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join(timeout=30)  # wait for thread with timeout

    if thread.is_alive():
        raise TimeoutError("Function call timed out after 30 seconds")

    result = result_queue.get_nowait()
    return cast(T, result)


def get_web_ui_port():
    web_ui_port = (
        get_arg("port") or int(dotenv.get_dotenv_value("WEB_UI_PORT", 0)) or 5000
    )
    return web_ui_port


def get_platform():
    return sys.platform


def is_windows():
    return get_platform() == "win32"


def get_terminal_executable():
    if is_windows():
        return "powershell.exe"
    else:
        return "/bin/bash"
