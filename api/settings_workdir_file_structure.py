from helpers.api import ApiHandler, Request, Response

from helpers import file_tree, files
from helpers.projects import capped_file_structure_setting


class SettingsWorkdirFileStructure(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        workdir_path = input.get("workdir_path", "")
        workdir_path = files.get_abs_path_development(workdir_path)
        if not workdir_path:
            raise Exception("workdir_path is required")

        # The project tree's ceilings: the limits are the request's, and file_tree
        # reads 0 as unlimited.
        limits = {key: input.get(f"workdir_{key}") for key in ("max_depth", "max_files", "max_folders", "max_lines")}
        tree = str(
            file_tree.file_tree(
                workdir_path,
                max_depth=capped_file_structure_setting(limits, "max_depth"),
                max_files=capped_file_structure_setting(limits, "max_files"),
                max_folders=capped_file_structure_setting(limits, "max_folders"),
                max_lines=capped_file_structure_setting(limits, "max_lines"),
                ignore=input.get("workdir_gitignore", "") or "",
                output_mode=file_tree.OUTPUT_MODE_STRING,
            )
        )

        if "\n" not in tree:
            tree += "\n # Empty"

        return {"data": tree}

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]
