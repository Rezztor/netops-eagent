from helpers.api import ApiHandler, Request, Response
from helpers.file_browser import FileBrowser
from helpers import runtime, files

class GetWorkDirFiles(ApiHandler):

    @classmethod
    def get_methods(cls):
        return ["GET"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        current_path = request.args.get("path", "") or "$WORK_DIR"
        if current_path == "$WORK_DIR":
            # "" is the browser's own root, which is now the caller's working
            # directory. This used to be "/eagent" -- the whole container --
            # which combined with a FileBrowser rooted at "/" made the default
            # view of "your files" every file the application could read.
            current_path = ""

        # browser = FileBrowser()
        # result = browser.get_files(current_path)
        result = await runtime.call_development_function(get_files, current_path)

        return {"data": result}


async def get_files(path):
    browser = FileBrowser()
    return browser.get_files(path)
