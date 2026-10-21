import initialize
from helpers import dotenv, extension, runtime
from helpers.api import csrf_protect, requires_auth
from helpers.print_style import PrintStyle
from helpers.server_startup import run_uvicorn_with_retries
from helpers.ui_server import UiServerRuntime, configure_process_environment


def run():
    configure_process_environment()
    PrintStyle().print("Initializing Python framework...")

    PrintStyle().print("Preparing the database...")
    prepare_database()

    PrintStyle().print("Checking for data migration...")
    run_migration_checks()

    PrintStyle().print("Checking administrator account...")
    ensure_admin_account()

    PrintStyle().print("Checking bundled integrations...")
    provision_bundled_integrations()

    PrintStyle().print("Preparing web server runtime...")
    server_runtime, host, port = prepare_web_runtime()

    PrintStyle().print("Initializing Rezztor EAgent components...")
    init_components()

    PrintStyle().print("Starting UI/API server...")
    start_web_server(server_runtime, host, port)


def provision_bundled_integrations() -> None:
    """Turn on what this deployment already shipped, once.

    A deployment that runs the Ansible executor has said it wants network
    automation; making an administrator repeat that in a settings form is a
    manual step with no decision in it. The admin's own choice, once made, is
    never overwritten -- see the plugin's `provisioning` module.

    A failure here is reported and swallowed. Unlike the database, nothing else
    depends on it: the instance runs perfectly well with the plugin off, and
    refusing to start over a convenience would trade a working product for a
    tidy one.
    """
    try:
        from plugins._ea_ansible.helpers import provisioning

        provisioning.enable_if_the_deployment_ships_an_executor()
    except Exception as error:
        PrintStyle.error(
            f"Network automation could not be enabled automatically: {error}\n"
            "The instance is otherwise fine; enable it in Plugins if you want it."
        )


def prepare_database() -> None:
    """Create the application role and bring the schema up to date.

    First, and before anything reads a setting or an account, because everything
    does. Accounts, settings, conversations and agent memory are all in
    PostgreSQL and there is no file fallback.

    It raises rather than warning. An instance that came up without this would
    serve a sign-in page it cannot authenticate against, and every request would
    fail on a role that does not exist -- which reads as "the application is
    broken" rather than "the database is not ready", and sends whoever is
    debugging it to the wrong place.
    """
    from helpers import db

    try:
        applied = db.bootstrap()
    except db.NotConfigured as error:
        PrintStyle.error(str(error))
        raise
    except Exception as error:  # noqa: BLE001
        PrintStyle.error(
            f"The database could not be prepared: {error}\n"
            "The application stores everything in PostgreSQL and will not start "
            "without it. Check that the database is reachable and that "
            "REZZTOR_DB_URL names an account that may create roles and tables."
        )
        raise
    if applied:
        PrintStyle().print(f"Applied migrations: {', '.join(applied)}")


def run_migration_checks() -> None:
    initialize.initialize_migration()


def ensure_admin_account() -> None:
    """Create the first administrator when the account store is empty.

    Authentication is mandatory, so a deployment with no accounts would be
    unreachable. Set REZZTOR_ADMIN_USER / REZZTOR_ADMIN_PASSWORD to choose the
    credentials; otherwise a strong password is generated and printed once.
    """
    from helpers import users

    try:
        users.ensure_seed_admin()
    except users.UserError as exc:
        PrintStyle.error(f"Could not initialize the administrator account: {exc}")
        raise


def prepare_web_runtime() -> tuple[UiServerRuntime, str, int]:
    host = (
        runtime.get_arg("host") or dotenv.get_dotenv_value("WEB_UI_HOST") or "localhost"
    )
    port = runtime.get_web_ui_port()
    server_runtime = UiServerRuntime.create()
    server_runtime.register_http_routes()
    server_runtime.register_transport_handlers()

    return server_runtime, host, port


def start_web_server(server_runtime: UiServerRuntime, host: str, port: int) -> None:
    run_uvicorn_with_retries(
        host=host,
        port=port,
        build_asgi_app=server_runtime.build_asgi_app,
        flush_callback=create_flush_callback(),
        access_log=server_runtime.access_log_enabled(),
        ws="wsproto",
    )


def create_flush_callback():
    def flush_and_shutdown_callback() -> None:
        """
        TODO(dev): add cleanup + flush-to-disk logic here.
        """
        return

    flush_ran = False

    def _run_flush(reason: str) -> None:
        nonlocal flush_ran
        if flush_ran:
            return
        flush_ran = True
        try:
            flush_and_shutdown_callback()
        except Exception as e:
            PrintStyle.warning(f"Shutdown flush failed ({reason}): {e}")

    return _run_flush


def init_components():
    init_chats = initialize.initialize_chats()
    init_chats.result_sync()

    initialize.initialize_mcp()
    initialize.initialize_job_loop()
    initialize.initialize_preload()


# The extension point is `_functions/__main__/init_components` however the server
# was started. The point's folder comes from the function's module, which is
# `__main__` when this file is run by path and `run_ui` when a delivered image
# starts it through its launcher; left to that, the image would skip the
# extensions that register the watchdogs.
init_components.__module__ = "__main__"
init_components = extension.extensible(init_components)


def main():
    # Its own function because a delivered image starts the server through a
    # two-line launcher: this file is compiled there, and a compiled module
    # cannot be run by path (docker/compile_python.py).
    runtime.initialize()
    dotenv.load_dotenv()
    run()


if __name__ == "__main__":
    main()
