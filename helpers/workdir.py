"""Resolving the agent's working directory for the current user.

workdir_path is a single administrator-managed setting, but the directory it
names is where agent-executed code reads and writes files. Sharing one directory
between accounts would let one user's agent read, overwrite or delete another's
work, so the configured path is treated as a base and each user gets their own
subdirectory beneath it.

The user segment is appended unconditionally rather than only for the default
path. An administrator who repoints workdir_path at a mounted volume still gets
per-user separation, instead of silently losing it by changing a setting that
does not mention users.
"""

from __future__ import annotations

import os

from helpers import files, user_scope
from helpers.errors import RepairableException


def scope_to_user(base: str, user_id: str | None = None) -> str:
    """Append the owner's segment to a configured workdir base.

    Kept separate from reading the setting so each caller keeps its own way of
    resolving the base -- several resolve it through their own indirection for
    projects or for testability, and routing them all through one settings read
    here would bypass that.
    """
    user_id = user_id or user_scope.get_current_user_id()
    if not base:
        return base
    if not user_scope.is_safe_user_id(user_id):
        # No resolvable owner: leave the base alone rather than inventing a
        # directory. Callers that must not share fail closed elsewhere.
        return base
    return files.get_abs_path(files.fix_dev_path(base), user_id)


def get_workdir_base() -> str:
    from helpers import settings

    configured = str(settings.get_settings().get("workdir_path") or "").strip()
    return configured or files.get_abs_path_dockerized("usr/workdir")


def get_workdir_path(user_id: str | None = None) -> str:
    """Working directory for one user, as a container-normalised path."""
    scoped = scope_to_user(get_workdir_base(), user_id)
    return files.normalize_container_path(scoped)


def user_file_roots(user_id: str | None = None) -> list[str]:
    """The folders a path taken from the model or a request may point into.

    The account's working directory first -- relative paths are read from it --
    then its uploads, downloads and skills, and the projects folder, which is
    shared by design. Nothing else: not the rest of `usr/users/<id>/`, where the
    approval records of network and cloud changes are kept, not another
    account's folder, not `usr/.env`, and not the application itself.

    No resolvable owner raises rather than answering with a shared directory.
    """
    user_id = user_id or user_scope.require_current_user_id()
    if not user_scope.is_safe_user_id(user_id):
        raise user_scope.UserScopeError(f"Refusing to build a path for user id {user_id!r}.")
    roots = [scope_to_user(get_workdir_base(), user_id)]
    roots += [
        user_scope.get_user_path(part, user_id=user_id)
        for part in (
            user_scope.WORKDIR_DIR,
            user_scope.UPLOADS_DIR,
            user_scope.DOWNLOADS_DIR,
            user_scope.SKILLS_DIR,
        )
    ]
    # `projects.PROJECTS_PARENT_DIR`, spelled out: importing helpers.projects
    # here would pull the agent into every module that only needs a path.
    roots.append(files.get_abs_path("usr/projects"))
    return [os.path.realpath(root) for root in roots]


#: Folders inside a project that are not the project's files. `.eaproj` holds
#: its secrets and Python the application loads. `.git` holds a configuration
#: git obeys -- `core.fsmonitor`, hooks, or as a file `gitdir: <anywhere>` -- and
#: the application runs git there whenever a project is opened.
PROJECT_PRIVATE_PARTS = frozenset({".eaproj", ".git"})


def is_project_private(real_path: str) -> bool:
    """True for a path inside one of a project's private folders. `real_path` is already resolved."""
    projects_root = os.path.realpath(files.get_abs_path("usr/projects"))
    return files.is_in_dir(real_path, projects_root) and not PROJECT_PRIVATE_PARTS.isdisjoint(
        real_path.split(os.sep)
    )


#: Every spelling `projects.PROJECT_AGENTS_MD_FILES` reads, in lower case.
PROJECT_INSTRUCTION_NAMES = frozenset({"agents.md", "agents.override.md"})


def cannot_change_project_instructions(real_path: str, user_id: str | None = None) -> bool:
    """True when `real_path` is a project's `AGENTS.md` and the account is not an administrator.

    That file, at the project's root and in any folder below it, is read into the prompt of every account that
    opens the project, so changing it is administration, as `api/projects.py` has it for the project's other
    instructions. Everybody may still read it. `real_path` is already resolved. For a write only.
    """
    from helpers import rbac, users

    projects_root = os.path.realpath(files.get_abs_path("usr/projects"))
    return (
        os.path.basename(real_path).lower() in PROJECT_INSTRUCTION_NAMES
        and files.is_in_dir(real_path, projects_root)
        and not users.holds_permission(user_id or user_scope.get_current_user_id(), rbac.PERM_SETTINGS_MANAGE)
    )


def confine_path(path: str, user_id: str | None = None, also: tuple[str, ...] = ()) -> str:
    """The real path of `path`, proven to be inside the account's own folders.

    The one check every tool and endpoint that opens a file named by the model
    or by a request goes through. These run in the application container, not
    on the runner, so an unchecked path reads `usr/.env`, rewrites an approval
    record, or opens another account's files -- for any role.

    `realpath` comes before the comparison, so `..`, an absolute path and a
    symlink are all judged by where they actually lead, and the path returned is
    the one that was judged. A project's `.eaproj` folder is refused even
    though the projects folder is allowed: it holds the project's secrets,
    which the agent is given masked, and Python the application itself loads --
    tools, extensions, script commands -- so writing there is running code in
    the web server. Its `.git` is refused for the same reason by another road
    (`PROJECT_PRIVATE_PARTS`).

    `also` adds roots for a caller that only reads one kind of file from
    somewhere else of the account's own -- the image loader and chat media.
    """
    roots = user_file_roots(user_id)
    raw = str(path or "").strip()
    candidate = files.fix_dev_path(raw) if os.path.isabs(raw) else os.path.join(roots[0], raw)
    real = os.path.realpath(candidate)
    allowed = roots + [os.path.realpath(root) for root in also]
    if not raw or is_project_private(real) or not any(files.is_in_dir(real, root) for root in allowed):
        raise RepairableException(
            f"{path!r} is outside the folders this account may use. Files can be read and "
            f"written in the working directory {files.normalize_container_path(roots[0])} "
            "(relative paths are read from it), in the account's own uploads, downloads "
            "and skills folders, and in the projects folder."
        )
    return real


def confine_read_path(path: str, user_id: str | None = None) -> str:
    """`confine_path` for a caller that only reads, plus the account's own saved tool results.

    A tool result of any length is saved to `chats/<conversation>/messages/<n>.txt` and its path is handed to the
    model beside the result (extensions/python/hist_add_tool_result), for the include marker and for a second
    look. That file is the account's own and nothing else under `chats/` is opened: the path must be exactly
    that shape, judged after `realpath`. Never used for a write.
    """
    try:
        return confine_path(path, user_id)
    except RepairableException:
        raw = str(path or "").strip()
        chats = os.path.realpath(user_scope.get_user_path(user_scope.CHATS_DIR, user_id=user_id))
        real = os.path.realpath(files.fix_dev_path(raw)) if os.path.isabs(raw) else ""
        parts = os.path.relpath(real, chats).split(os.sep) if real and files.is_in_dir(real, chats) else []
        if len(parts) == 3 and parts[1] == "messages":
            return real
        raise
