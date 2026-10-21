"""
SMTP email sender. 

No agent/tool dependencies.
"""

import asyncio
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dataclasses import dataclass

from helpers.errors import format_error
from helpers.print_style import PrintStyle


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------

SMTP_TIMEOUT: int = 30


def _tls_context() -> ssl.SSLContext:
    """Verified TLS, as `helpers/mailer.py` uses.

    Without a context smtplib checks neither the certificate nor the host
    name, so the mailbox password and every reply went to whoever answered.
    """
    return ssl.create_default_context()

@dataclass
class SmtpConfig:
    server: str
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True


# ------------------------------------------------------------------
# Send reply
# ------------------------------------------------------------------

def reply_html(body: str) -> str:
    """The HTML part of a reply: the model's text through Markdown, with what a mail has no use for removed.

    Markdown passes raw HTML through, and the text is the model's, written after reading a mail from outside.
    Without the cleaner a sender could have this mailbox send a script, a form or a frame of their choosing.
    """
    import markdown
    from lxml_html_clean import Cleaner

    html = markdown.markdown(body, extensions=['extra', 'nl2br'])
    return Cleaner().clean_html(html) if html.strip() else html


async def send_reply(
    config: SmtpConfig,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
    references: str = "",
    attachments: list[tuple[str, bytes]] | None = None,
) -> str | None:
    loop = asyncio.get_event_loop()

    def _sync_send():
        html_body = reply_html(body)
        html_content = f"""
        <html>
        <head>
        <style>
            body {{ font-family: sans-serif; line-height: 1.5; }}
            blockquote {{ border-left: 4px solid #ccc; margin: 0; padding-left: 1em; color: #666; }}
            pre {{ background-color: #f6f8fa; padding: 1em; border-radius: 4px; overflow-x: auto; }}
            code {{ font-family: monospace; background-color: #f6f8fa; padding: 0.2em 0.4em; border-radius: 3px; }}
            pre code {{ padding: 0; background-color: transparent; }}
        </style>
        </head>
        <body>
        {html_body}
        </body>
        </html>
        """

        if attachments:
            msg = MIMEMultipart("mixed")
            
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText(body, "plain", "utf-8"))
            alt_part.attach(MIMEText(html_content, "html", "utf-8"))
            msg.attach(alt_part)
            
            for filename, content in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(content)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={filename}",
                )
                msg.attach(part)
        else:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

        msg["From"] = config.username
        msg["To"] = to
        msg["Subject"] = subject

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to

        if config.use_tls:
            with smtplib.SMTP(config.server, config.port, timeout=SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=_tls_context())
                server.ehlo()
                server.login(config.username, config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(config.server, config.port, timeout=SMTP_TIMEOUT, context=_tls_context()) as server:
                server.login(config.username, config.password)
                server.send_message(msg)

    try:
        await loop.run_in_executor(None, _sync_send)
        PrintStyle.success(f"Email sent to {to}: {subject}")
        return None
    except Exception as e:
        # The traceback names server paths: it stays in the server log. The
        # caller gets the class and message, which is what reaches the agent.
        PrintStyle.error(f"Email send failed: {format_error(e)}")
        return f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------
# Connection test (auth only, no email sent)
# ------------------------------------------------------------------

async def test_smtp(config: SmtpConfig) -> str | None:
    loop = asyncio.get_event_loop()

    def _sync_test():
        if config.use_tls:
            with smtplib.SMTP(config.server, config.port, timeout=SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=_tls_context())
                server.ehlo()
                server.login(config.username, config.password)
        else:
            with smtplib.SMTP_SSL(config.server, config.port, timeout=SMTP_TIMEOUT, context=_tls_context()) as server:
                server.login(config.username, config.password)

    try:
        await loop.run_in_executor(None, _sync_test)
        return None
    except Exception as e:
        return format_error(e)
