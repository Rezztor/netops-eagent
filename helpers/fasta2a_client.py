import asyncio
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional
from helpers import network
from helpers.print_style import PrintStyle

try:
    from fasta2a.client import A2AClient  # type: ignore
    import httpx  # type: ignore
    FASTA2A_CLIENT_AVAILABLE = True
except ImportError:
    FASTA2A_CLIENT_AVAILABLE = False
    PrintStyle.warning("FastA2A client not available. Agent-to-agent communication disabled.")

_PRINTER = PrintStyle(italic=True, font_color="cyan", padding=False)


if FASTA2A_CLIENT_AVAILABLE:

    class _PinnedTransport(httpx.AsyncHTTPTransport):  # type: ignore[name-defined]
        """Connect to the address `connect_to_agent` approved, for every request.

        `helpers/network.py`'s `_PinnedAdapter`, for httpx: letting httpx
        resolve the name again is a DNS-rebinding gap, a zone answering a
        public address to the check and a private one to the connect. Only the
        TCP connect is pinned; the Host header, SNI and certificate check still
        name the URL's host. A request for any other host -- a redirect, were
        one ever followed -- is refused rather than resolved.

        With no address and a `proxy`, the proxy resolves the name and only
        the host check applies (`connect_to_agent`).
        """

        def __init__(self, host: str, address: Optional[str], **kwargs: Any):
            super().__init__(**kwargs)
            self._host = host
            self._address = address

        async def handle_async_request(self, request):  # type: ignore[override]
            if request.url.host != self._host:
                raise httpx.ConnectError(  # type: ignore[name-defined]
                    f"Refused: {request.url.host} is not the agent this connection checked",
                    request=request,
                )
            if self._address:
                request.url = request.url.copy_with(host=self._address)
                request.extensions = {**request.extensions, "sni_hostname": self._host}
            return await super().handle_async_request(request)


class AgentConnection:
    """Helper class for connecting to and communicating with other Rezztor EAgent instances via FastA2A."""

    def __init__(
        self,
        agent_url: str,
        timeout: int = 30,
        token: Optional[str] = None,
        address: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        """Initialize connection to an agent.

        Args:
            agent_url: The base URL of the agent (e.g., "https://agent.example.com")
            timeout: Request timeout in seconds
            address: The checked address every request connects to (`connect_to_agent` passes it)
            proxy: The egress proxy every request goes through instead (`connect_to_agent` passes it)
        """
        if not FASTA2A_CLIENT_AVAILABLE:
            raise RuntimeError("FastA2A client not available")

        # Ensure scheme is present
        if not agent_url.startswith(('http://', 'https://')):
            agent_url = 'http://' + agent_url

        self.agent_url = agent_url.rstrip('/')
        self.timeout = timeout
        # Auth headers
        if token is None:
            import os
            token = os.getenv("A2A_TOKEN")
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-API-KEY"] = token
        # No redirects: connect_to_agent checked this URL's host, and a redirect would go somewhere it did not check.
        transport = (
            _PinnedTransport(httpx.URL(self.agent_url).host, address, proxy=proxy)  # type: ignore
            if address or proxy
            else None
        )
        self._http_client = httpx.AsyncClient(  # type: ignore
            timeout=timeout, headers=headers, follow_redirects=False, transport=transport
        )
        self._a2a_client = A2AClient(base_url=self.agent_url, http_client=self._http_client)  # type: ignore
        self._agent_card: Optional[Dict[str, Any]] = None
        # Track conversation context automatically
        self._context_id: Optional[str] = None

    async def get_agent_card(self) -> Dict[str, Any]:
        """Retrieve the agent card from the remote agent."""
        if self._agent_card is None:
            try:
                response = await self._http_client.get(f"{self.agent_url}/.well-known/agent.json")
                response.raise_for_status()
                self._agent_card = response.json()
                _PRINTER.print(f"Retrieved agent card from {self.agent_url}")
                _PRINTER.print(f"Agent: {self._agent_card.get('name', 'Unknown')}") # type: ignore
                _PRINTER.print(f"Description: {self._agent_card.get('description', 'No description')}") # type: ignore
            except Exception as e:
                # Fallback: if URL contains '/a2a', try root path without it
                if "/a2a" in self.agent_url:
                    root_url = self.agent_url.split("/a2a", 1)[0]
                    try:
                        response = await self._http_client.get(f"{root_url}/.well-known/agent.json")
                        response.raise_for_status()
                        self._agent_card = response.json()
                        _PRINTER.print(f"Retrieved agent card from {root_url}")
                    except Exception:
                        pass  # swallow, will re-raise below
                _PRINTER.print(f"[!] Could not connect to {self.agent_url}\n    → Ensure the server is running and reachable.\n    → Full error: {e}")
                raise RuntimeError(f"Could not retrieve agent card: {e}")

        return self._agent_card  # type: ignore

    async def send_message(
        self,
        message: str,
        attachments: Optional[List[str]] = None,
        context_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send a message to the remote agent and return task response."""
        if not self._agent_card:
            await self.get_agent_card()

        # Re-use context automatically if caller did not supply one
        if context_id is None:
            context_id = self._context_id

        # Build message parts
        parts = [{'kind': 'text', 'text': message}]

        if attachments:
            for attachment in attachments:
                file_part = {'kind': 'file', 'file': {'uri': attachment}}
                parts.append(file_part)  # type: ignore

        # Construct A2A message
        a2a_message = {
            'role': 'user',
            'parts': parts,
            'kind': 'message',
            'message_id': str(uuid.uuid4())
        }

        if context_id is not None:
            a2a_message['context_id'] = context_id

        # Send using the message/send method (not send_task)
        try:
            response = await self._a2a_client.send_message(
                message=a2a_message,  # type: ignore
                metadata=metadata,
                configuration={'accepted_output_modes': ['application/json', 'text/plain'], 'blocking': True}  # type: ignore
            )

            # Persist context id for subsequent calls
            try:
                ctx = response.get('result', {}).get('context_id')  # type: ignore[index]
                if isinstance(ctx, str):
                    self._context_id = ctx
            except Exception:
                pass  # ignore if structure differs
            return response  # type: ignore
        except Exception as e:
            _PRINTER.print(f"[A2A] Error sending message: {e}")
            raise

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        """Get the status and results of a task.

        Args:
            task_id: The ID of the task to query

        Returns:
            Dictionary containing the task information
        """
        try:
            response = await self._a2a_client.get_task(task_id)  # type: ignore
            return response  # type: ignore
        except Exception as e:
            _PRINTER.print(f"Failed to get task {task_id}: {e}")
            raise RuntimeError(f"Failed to get task: {e}")

    async def wait_for_completion(self, task_id: str, poll_interval: int = 2, max_wait: int = 300) -> Dict[str, Any]:
        """Wait for a task to complete and return the final result.

        Args:
            task_id: The ID of the task to wait for
            poll_interval: How often to check task status (seconds)
            max_wait: Maximum time to wait (seconds)

        Returns:
            Dictionary containing the completed task information
        """
        import asyncio

        waited = 0
        while waited < max_wait:
            task_info = await self.get_task(task_id)

            if 'result' in task_info:
                task = task_info['result']
                status = task.get('status', {})
                state = status.get('state', 'unknown')

                if state in ['completed', 'failed', 'canceled']:
                    _PRINTER.print(f"Task {task_id} finished with state: {state}")
                    return task_info
                else:
                    _PRINTER.print(f"Task {task_id} status: {state}")

            await asyncio.sleep(poll_interval)
            waited += poll_interval

        raise TimeoutError(f"Task {task_id} did not complete within {max_wait} seconds")

    async def close(self):
        """Close the HTTP client connection."""
        await self._http_client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


async def connect_to_agent(agent_url: str, timeout: int = 30) -> AgentConnection:
    """Create a connection to a remote agent.

    Args:
        agent_url: The base URL of the agent
        timeout: Request timeout in seconds

    Returns:
        AgentConnection instance
    """
    if not agent_url.startswith(('http://', 'https://')):
        agent_url = 'http://' + agent_url
    # The URL is the model's to choose, so a prompt can aim it at the Compose network (postgres, searxng),
    # loopback or a cloud metadata address. Only a public address is reached, and it is the address
    # checked here that every request of this connection goes to (`_PinnedTransport`).
    proxy = _proxy_for(agent_url)
    if proxy:
        # The customer's egress proxy (HTTP(S)_PROXY, minus NO_PROXY) resolves
        # the name in its own network, where this container's Compose names
        # mean nothing; there is no address here to pin.
        await asyncio.to_thread(network.validate_proxied_http_url, agent_url)
        connection = AgentConnection(agent_url, timeout, proxy=proxy)
    else:
        addresses = await asyncio.to_thread(network.validate_public_http_url, agent_url)
        connection = AgentConnection(agent_url, timeout, address=str(addresses[0]))
    # Verify connection by retrieving agent card
    await connection.get_agent_card()
    return connection


def _proxy_for(url: str) -> Optional[str]:
    """The proxy the environment names for this URL, or None (NO_PROXY honoured)."""
    parsed = urllib.parse.urlparse(url)
    proxies = urllib.request.getproxies_environment()
    if not parsed.hostname or urllib.request.proxy_bypass_environment(parsed.hostname, proxies):
        return None
    return proxies.get(parsed.scheme) or None


def is_client_available() -> bool:
    """Check if FastA2A client is available."""
    return FASTA2A_CLIENT_AVAILABLE
