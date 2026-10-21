from helpers.api import ApiHandler, Request, Response
from helpers import user_scope
from helpers import files
from helpers.file_browser import ACCOUNT_QUOTA_BYTES, QuotaExceeded, account_usage, quota_lock, stream_size
from helpers.security import safe_filename


class UploadFile(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if "file" not in request.files:
            raise Exception("No file part")

        file_list = request.files.getlist("file")  # Handle multiple files
        saved_filenames = []

        # The per-user upload folder is created on demand: unlike the old shared
        # usr/uploads it does not exist until this account first uploads.
        user_scope.ensure_user_dir(user_scope.UPLOADS_DIR)
        # Check and writes under the account's lock, so concurrent uploads
        # cannot each pass the check against the same free space.
        with quota_lock():
            used = account_usage()

            for file in file_list:
                if file and self.allowed_file(file.filename):  # Check file type
                    if not file.filename:
                        continue
                    filename = safe_filename(file.filename)
                    if not filename:
                        continue
                    size = stream_size(file.stream)
                    if used + size > ACCOUNT_QUOTA_BYTES:
                        raise QuotaExceeded("Storage quota reached: delete some files first")
                    file.save(user_scope.get_user_path(user_scope.UPLOADS_DIR, filename))
                    used += size
                    saved_filenames.append(filename)

        return {"filenames": saved_filenames}  # Return saved filenames


    def allowed_file(self,filename):
        return True
        # ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "txt", "pdf", "csv", "html", "json", "md"}
        # return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS