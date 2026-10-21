from agent import AgentContext, UserMessage
from helpers.api import ApiHandler, Request, Response

from helpers import user_scope
from helpers import errors, files, extension, guids, message_queue as mq
import json
import os
from helpers.security import safe_filename
from helpers.file_browser import check_account_quota, quota_lock, stream_size
from helpers.defer import DeferredTask


class Message(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        # JSON or multipart, nothing else. A form post used to reach
        # get_json(), whose 415 surfaced as a 500 with Werkzeug's text in it.
        if not (request.is_json or (request.content_type or "").startswith("multipart/form-data")):
            return Response(
                response=json.dumps({"ok": False, "error": "Send the message as JSON or multipart/form-data."}),
                status=415,
                mimetype="application/json",
            )
        task, context = await self.communicate(input=input, request=request)
        return await self.respond(task, context)

    async def respond(self, task: DeferredTask, context: AgentContext):
        try:
            result = await task.result()  # type: ignore
        except Exception as e:
            # The turn failed and the chat already says why in one sentence
            # (handle_exception's log_failure). The caller gets that sentence,
            # not a 500 whose body is the exception text -- for a runner
            # failure, a slot name and a path inside the server.
            return Response(
                response=json.dumps({"ok": False, "error": errors.user_message(e), "context": context.id}),
                status=502,
                mimetype="application/json",
            )
        return {
            "message": result,
            "context": context.id,
        }

    async def communicate(self, input: dict, request: Request):
        # Handle both JSON and multipart/form-data
        if request.content_type.startswith("multipart/form-data"):
            text = request.form.get("text", "")
            ctxid = request.form.get("context", "")
            message_id = request.form.get("message_id", None)
            answers = request.form.get("answers", "")
            attachments = request.files.getlist("attachments")
            attachment_paths = []

            upload_folder_ext = user_scope.get_user_path(user_scope.UPLOADS_DIR)
            upload_folder_int = files.normalize_container_path(upload_folder_ext)

            if attachments:
                # The account quota, the same as api/upload.py: attachments
                # land in the same uploads folder. Checked for the lot before
                # any is saved, so a refusal leaves nothing half-written.
                # Under the account's lock until written: concurrent messages
                # must not each pass the check against the same free space.
                with quota_lock():
                    check_account_quota(sum(stream_size(a.stream) for a in attachments))
                    os.makedirs(upload_folder_ext, exist_ok=True)
                    for attachment in attachments:
                        if attachment.filename is None:
                            continue
                        filename = safe_filename(attachment.filename)
                        if not filename:
                            continue
                        save_path = files.get_abs_path(upload_folder_ext, filename)
                        attachment.save(save_path)
                        attachment_paths.append(os.path.join(upload_folder_int, filename))
        else:
            # Handle JSON request as before
            input_data = request.get_json()
            text = input_data.get("text", "")
            ctxid = input_data.get("context", "")
            message_id = input_data.get("message_id", None)
            # The interaction card this message replies to, when it was sent by
            # clicking one. Carried to the log item's kvps and nowhere else: it
            # is a fact about the record, not an instruction to the agent.
            answers = str(input_data.get("answers", "") or "")[:64]
            attachment_paths = []

        # Now process the message
        message = text

        # Obtain agent context. No context means a new conversation: an empty
        # id used to resolve to the caller's first chat, so a message meant to
        # start fresh was appended to whatever they had talked about before.
        context = self.use_context(ctxid or guids.generate_id())

        # call extension point, alow it to modify data
        data = { "message": message, "attachment_paths": attachment_paths }
        await extension.call_extensions_async("user_message_ui", agent=context.get_agent(), data=data)
        message = data.get("message", "")
        attachment_paths = data.get("attachment_paths", [])

        # Store attachments in agent data
        # context.root_agent.set_data("attachments", attachment_paths)

        # Log to console and UI using helper function
        mq.log_user_message(context, message, attachment_paths, message_id, answers=answers)

        return context.communicate(UserMessage(message=message, attachments=attachment_paths, id=message_id or "")), context
