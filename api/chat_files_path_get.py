from helpers.api import ApiHandler, Request, Response
from helpers import files, projects, workdir


class GetChatFilesPath(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        ctxid = input.get("ctxid", "")
        if not ctxid:
            raise Exception("No context id provided")
        context = self.use_context(ctxid, create_if_not_exists=False)

        project_name = projects.get_context_project_name(context)
        if project_name:
            folder = files.normalize_container_path(projects.get_project_folder(project_name))
        else:
            # The account's own folder under the shared base, as the agent and the file tools see it.
            folder = workdir.get_workdir_path(getattr(context, "owner_id", None))

        return {
            "ok": True,
            "path": folder,
        }