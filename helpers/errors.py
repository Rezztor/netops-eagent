import re
import traceback
import asyncio
from typing import Literal


def handle_error(e: Exception):
    # if asyncio.CancelledError, re-raise
    if isinstance(e, asyncio.CancelledError):
        raise e


def error_text(e: Exception):
    return str(e)


def format_error(
    e: Exception,
    start_entries=20,
    end_entries=15,
    error_message_position: Literal["top", "bottom", "none"] = "top",
):
    # format traceback from the provided exception instead of the most recent one
    traceback_text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    # Split the traceback into lines
    lines = traceback_text.split("\n")

    if not start_entries and not end_entries:
        trimmed_lines = []
    else:

        # Find all "File" lines
        file_indices = [
            i for i, line in enumerate(lines) if line.strip().startswith("File ")
        ]

        # If we found at least one "File" line, trim the middle if there are more than start_entries+end_entries lines
        if len(file_indices) > start_entries + end_entries:
            start_index = max(0, len(file_indices) - start_entries - end_entries)
            trimmed_lines = (
                lines[: file_indices[start_index]]
                + [
                    f"\n>>>  {len(file_indices) - start_entries - end_entries} stack lines skipped <<<\n"
                ]
                + lines[file_indices[start_index + end_entries] :]
            )
        else:
            # If no "File" lines found, or not enough to trim, just return the original traceback
            trimmed_lines = lines

    # Find the error message at the end
    error_message = ""
    for line in reversed(lines):
        # match both simple errors and module.path.Error patterns
        if re.match(r"[\w\.]+Error:\s*", line):
            error_message = line
            break

    if error_message and error_message_position in ("top", "bottom", "none"):
        for i in range(len(trimmed_lines) - 1, -1, -1):
            if trimmed_lines[i].strip() == error_message.strip():
                trimmed_lines = trimmed_lines[:i] + trimmed_lines[i + 1 :]
                break

    # Combine the trimmed traceback with the error message
    if not trimmed_lines:
        result = "" if error_message_position == "none" else error_message
    else:
        result = "Traceback (most recent call last):\n" + "\n".join(trimmed_lines)

    if error_message and error_message_position == "top":
        result = f"{error_message}\n\n{result}" if result else error_message
    elif error_message and error_message_position == "bottom":
        result = f"{result}\n\n{error_message}" if result else error_message

    # at least something
    if not result:
        result = str(e)

    return result


_KEY_REFUSED = "The model provider refused the key it was given. An administrator can check it on the Models page."
NO_MODEL = "No chat model is configured, so the agent cannot answer. An administrator can set one up on the Models page."

# Exception class names, not imports: the classes live in a plugin and in
# LiteLLM, and this module is imported before either is loaded.
_PLAIN_MESSAGES: dict[str, str] = {
    "EmailSendError": "The reply email could not be sent. If it keeps failing, ask an administrator to check the mail settings.",
    "RunnerUnavailable": "The code runner is not available, so the agent could not run code. Ask an administrator to check the runner.",
    "AuthenticationError": _KEY_REFUSED,
    "PermissionDeniedError": _KEY_REFUSED,
    "RateLimitError": "The model provider is limiting requests right now. Wait a minute and try again.",
    "APIConnectionError": "The model provider could not be reached. Try again; if it repeats, ask an administrator.",
    "Timeout": "The model provider took too long to answer. Try again; if it repeats, ask an administrator.",
    "ServiceUnavailableError": "The model provider is unavailable right now. Try again in a few minutes.",
    "ContextWindowExceededError": "This conversation is too long for the model. Compact it or start a new one.",
}
PLAIN_FALLBACK = "Something went wrong while the agent was working. Try again; if it repeats, ask an administrator."


def user_message(e: BaseException) -> str:
    """One sentence a person can act on, for an exception that ended a turn.

    Never the exception's own text: that names hosts, files and slots, and is
    for whoever runs the server (`details_for`), not for whoever sent the chat.
    """
    if isinstance(e, HandledException) and e.args and isinstance(e.args[0], BaseException):
        e = e.args[0]
    for cls in type(e).__mro__:
        if cls.__name__ in _PLAIN_MESSAGES:
            message = _PLAIN_MESSAGES[cls.__name__]
            # A call with no key at all fails the same way as a refused one.
            if message is _KEY_REFUSED and not _chat_model_configured():
                return NO_MODEL
            return message
    return PLAIN_FALLBACK


def _chat_model_configured() -> bool:
    try:
        from plugins._model_config.helpers import model_config

        return model_config.is_chat_model_configured()
    except Exception:
        return True  # unknown: keep the message that was always given


def details_for(context, e: BaseException) -> str:
    """The traceback, when the conversation belongs to an administrator; else "".

    Resolved from the owner, like the tool gate, because an agent fails on a
    background thread with no request to read a session from.
    """
    try:
        from helpers import rbac, tool_permissions

        if rbac.is_admin(tool_permissions.role_for_context(context)):
            return format_error(e)
    except Exception:
        pass
    return ""


def log_failure(context, e: BaseException, *, type: str = "error", heading: str = ""):
    """Log a failure to the chat as one plain line, details only for admins.

    The server's own output always gets the traceback.
    """
    from helpers.print_style import PrintStyle

    PrintStyle.error(format_error(e))
    details = details_for(context, e)
    return context.log.log(
        type=type,
        heading=heading,
        content=user_message(e),
        kvps={"details": details} if details else None,
    )


class RepairableException(Exception):
    """An exception type indicating errors that can be surfaced to the LLM for potential self-repair."""

    pass


class InterventionException(Exception):
    """An exception type raised on user intervention, skipping rest of message loop iteration."""

    pass


class HandledException(Exception):
    pass
