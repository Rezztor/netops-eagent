import os
import shutil

from helpers.api import ApiHandler, Request, Response, send_file
from helpers import backup_crypto
from helpers.backup import BackupService
from helpers.persist_chat import save_tmp_chats


def _audit(event: str, **fields):
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class BackupCreate(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            # Get input parameters
            include_patterns = input.get("include_patterns", [])
            exclude_patterns = input.get("exclude_patterns", [])
            include_hidden = input.get("include_hidden", True)
            backup_name = BackupService.safe_backup_name(input.get("backup_name"))
            # Checked first: no archive is built for a backup that could not be sealed.
            passphrase = backup_crypto.check_passphrase(input.get("passphrase"))

            # Support legacy string patterns format for backward compatibility
            patterns_string = input.get("patterns", "")
            if patterns_string and not include_patterns and not exclude_patterns:
                # Parse legacy format
                lines = [line.strip() for line in patterns_string.split('\n') if line.strip() and not line.strip().startswith('#')]
                for line in lines:
                    if line.startswith('!'):
                        exclude_patterns.append(line[1:])
                    else:
                        include_patterns.append(line)

            # Save all chats to the chats folder
            save_tmp_chats()

            # Create backup service and generate backup
            backup_service = BackupService()
            sealed_path = await backup_service.create_backup(
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                include_hidden=include_hidden,
                backup_name=backup_name,
                passphrase=passphrase,
            )

            # Who took a full-instance archive, and under what name. Counts of
            # patterns, never the patterns themselves or archive bytes.
            _audit(
                "backup.create",
                backup_name=backup_name,
                include_count=len(include_patterns),
                exclude_count=len(exclude_patterns),
                include_hidden=bool(include_hidden),
            )

            # The archive is every user's chats and a dump of the database
            # (sealed, but still), so it must not outlive the download. Unlink
            # it once it is open: the response streams from the handle and the
            # space is freed when the server closes it. Not
            # response.call_on_close -- send_file's response is passthrough and
            # never runs those callbacks.
            archive = open(sealed_path, "rb")
            shutil.rmtree(os.path.dirname(sealed_path), ignore_errors=True)

            # Return file for download
            return send_file(
                archive,
                as_attachment=True,
                download_name=f"{backup_name}{backup_crypto.EXTENSION}",
                mimetype='application/octet-stream'
            )

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
