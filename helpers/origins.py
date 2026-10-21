"""Origin normalization helpers shared by the CSRF check and the WebSocket
origin validator."""

from urllib.parse import urlparse


_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
}


def origin_from_url(value):
    """Normalize a URL or Origin header to scheme://host[:port]."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.hostname:
        return None

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None

    origin = f"{scheme}://{host}"
    if port and port != _DEFAULT_PORTS.get(scheme):
        origin += f":{port}"
    return origin
