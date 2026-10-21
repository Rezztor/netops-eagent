from helpers.api import ApiHandler, Input, Output, Request, Response
from helpers import login, projects, rbac
from helpers.notification import NotificationManager, NotificationType, NotificationPriority


def _audit(event: str, **fields):
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


def _own_mcp_servers_only(project: dict) -> bool:
    """Drop `mcp_servers` from a request that may not set it. True if it may.

    A project's MCP servers are started by the application for whoever activates
    the project, and a stdio entry is a command line. Setting the instance's own
    list needs `settings.manage` (`mcp_servers_apply`); this endpoint is open to
    every account and took the same document, so the lowest role could run a
    process in the web server by saving a project and opening a chat in it.
    """
    allowed = login.has_permission(rbac.PERM_SETTINGS_MANAGE)
    if not allowed:
        project.pop("mcp_servers", None)
    return allowed


# Projects live in one folder for the whole instance and every account can
# read and activate them (ADR 0034: a project has no owner). Changing one
# therefore changes every account's agent -- its instructions, its secrets, its
# files -- so that is administration, as agent profiles and knowledge are.
WRITE_ACTIONS = frozenset({"create", "clone", "update", "delete"})


class Projects(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        action = input.get("action", "")
        ctxid = input.get("context_id", None)

        administrator = login.has_permission(rbac.PERM_SETTINGS_MANAGE)
        if action in WRITE_ACTIONS and not administrator:
            return {
                "ok": False,
                "error": "Only an administrator can create, change or delete a project.",
            }

        if ctxid:
            _context = self.use_context(ctxid)

        try:
            if action == "list":
                data = self.get_active_projects_list()
            elif action == "list_options":
                data = self.get_active_projects_options()
            elif action == "load":
                data = self.load_project(input.get("name", None))
                if not administrator:
                    # A server entry carries its headers and environment.
                    data["mcp_servers"] = projects.DEFAULT_MCP_SERVERS_CONFIG
                if not login.has_permission(rbac.PERM_TOOL_CODE_EXECUTION):
                    # Masked values, but the names are the aliases, and only an
                    # account that may run code resolves them (ADR 0086).
                    data["secrets"] = ""
            elif action == "create":
                data = self.create_project(input.get("project", None))
            elif action == "clone":
                data = self.clone_project(input.get("project", None))
            elif action == "update":
                data = self.update_project(input.get("project", None))
            elif action == "delete":
                data = self.delete_project(input.get("name", None))
            elif action == "activate":
                data = self.activate_project(ctxid, input.get("name", None))
            elif action == "deactivate":
                data = self.deactivate_project(ctxid)
            elif action == "file_structure":
                data = self.get_file_structure(input.get("name", None), input.get("settings"))
            else:
                raise Exception("Invalid action")

            if action in WRITE_ACTIONS:
                # Who created, cloned, changed or deleted a shared project.
                # The project name only — never instructions, secrets or MCP config.
                project = input.get("project") if isinstance(input.get("project"), dict) else {}
                _audit(
                    f"project.{action}",
                    project=str(
                        (project.get("name") if action != "delete" else None)
                        or input.get("name")
                        or (data.get("name") if isinstance(data, dict) else "")
                        or ""
                    ),
                )

            return {
                "ok": True,
                "data": data,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
            }

    def get_active_projects_list(self):
        return projects.get_active_projects_list()

    def get_active_projects_options(self):
        items = projects.get_active_projects_list() or []
        return [
            {"key": p.get("name", ""), "label": p.get("title", "") or p.get("name", "")}
            for p in items
            if p.get("name")
        ]

    def create_project(self, project: dict|None):
        if project is None:
            raise Exception("Project data is required")
        _own_mcp_servers_only(project)
        data = projects.BasicProjectData(**project)
        name = projects.create_project(project["name"], data)
        return projects.load_edit_project_data(name)

    def clone_project(self, project: dict|None):
        if project is None:
            raise Exception("Project data is required")
        git_url = project.get("git_url", "")
        git_token = project.get("git_token", "")
        if not git_url:
            raise Exception("Git URL is required")
        may_set_mcp = _own_mcp_servers_only(project)
        
        # Progress notification
        notification = NotificationManager.send_notification(
            NotificationType.PROGRESS,
            NotificationPriority.NORMAL,
            f"Cloning repository...",
            "Git Clone",
            display_time=999,
            group="git_clone"
        )
        
        try:
            data = projects.BasicProjectData(**project)
            name = projects.clone_git_project(project["name"], git_url, git_token, data)
            if not may_set_mcp:
                # The repository can carry its own .eaproj/mcp_servers.json,
                # which is the same document arriving by another road.
                projects.save_project_mcp_servers(name, projects.DEFAULT_MCP_SERVERS_CONFIG)
            
            # Success notification
            NotificationManager.send_notification(
                NotificationType.SUCCESS,
                NotificationPriority.NORMAL,
                f"Repository cloned successfully",
                "Git Clone",
                display_time=3,
                group="git_clone"
            )
            return projects.load_edit_project_data(name)
        except Exception as e:
            # Error notification
            NotificationManager.send_notification(
                NotificationType.ERROR,
                NotificationPriority.HIGH,
                f"Clone failed: {str(e)}",
                "Git Clone",
                display_time=5,
                group="git_clone"
            )
            raise

    def load_project(self, name: str|None):
        if name is None:
            raise Exception("Project name is required")
        return projects.load_edit_project_data(name)

    def update_project(self, project: dict|None):
        if project is None:
            raise Exception("Project data is required")
        _own_mcp_servers_only(project)
        data = projects.EditProjectData(**project)
        name = projects.update_project(project["name"], data)
        return projects.load_edit_project_data(name)

    def delete_project(self, name: str|None):
        if name is None:
            raise Exception("Project name is required")
        return projects.delete_project(name)

    def activate_project(self, context_id: str|None, name: str|None):
        if not context_id:
            raise Exception("Context ID is required")
        if not name:
            raise Exception("Project name is required") 
        return projects.activate_project(context_id, name)

    def deactivate_project(self, context_id: str|None):
        if not context_id:
            raise Exception("Context ID is required")
        return projects.deactivate_project(context_id)

    def get_file_structure(self, name: str|None, settings: dict|None):
        if not name:
            raise Exception("Project name is required")
        # project data
        basic_data = projects.load_basic_project_data(name)
        # override file structure settings
        if settings:
            basic_data["file_structure"] = settings # type: ignore
        # get structure
        return projects.get_file_structure(name, basic_data)