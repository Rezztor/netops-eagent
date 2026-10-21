from helpers.extension import Extension
import re


class UnsecuredConnectionCheck(Extension):
    """Warn when sign-in credentials would travel over an unencrypted connection.

    Authentication is mandatory in Rezztor EAgent, so the old "no credentials
    configured" warning no longer has a case to detect. What remains relevant is
    the transport: users type a password into this UI, and over plain HTTP from a
    non-local address that password crosses the network in the clear.
    """

    async def execute(self, banners: list = [], frontend_context: dict = {}, **kwargs):
        hostname = frontend_context.get("hostname", "")
        protocol = frontend_context.get("protocol", "")

        is_local = self._is_localhost(hostname)
        is_https = protocol == "https:"

        if not is_local and not is_https:
            banners.append({
                "id": "credentials-unencrypted",
                "type": "warning",
                "priority": 90,
                "title": "Connection Is Not Encrypted",
                "html": """You are signing in to Rezztor EAgent over plain HTTP from a
                         non-local address, so your password is transmitted in clear text.
                         Terminate TLS in front of this instance before using it beyond
                         local development.""",
                "dismissible": True,
                "source": "backend"
            })

    def _is_localhost(self, hostname: str) -> bool:
        local_patterns = ["localhost", "127.0.0.1", "::1", "0.0.0.0"]

        if hostname in local_patterns:
            return True

        # RFC1918 private ranges
        if re.match(r"^192\.168\.\d{1,3}\.\d{1,3}$", hostname):
            return True
        if re.match(r"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$", hostname):
            return True
        if re.match(r"^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$", hostname):
            return True

        # .local domains
        if hostname.endswith(".local"):
            return True

        return False
