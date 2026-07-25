"""
Synchronous bridge from the EnergyPlus callback thread to the FastMCP server.

The MCP client API is asyncio-native; EnergyPlus callbacks are plain synchronous
C-invoked functions.  `MCPToolBridge` owns a private event loop on a daemon
thread, keeps one long-lived MCP session open across the whole simulation (so we
pay the handshake once, not once per control interval), and exposes a blocking
`call()` with a hard timeout.

If the session cannot be established, or a call times out, the bridge degrades
to in-process dispatch from `mcp_tools.TOOL_REGISTRY`.  The control loop keeps
running; only the protocol hop is lost.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional

from config import SETTINGS
from eco_loop.mcp_tools import call_tool_direct


def _coerce_result(result: Any) -> Dict[str, Any]:
    """Normalise a FastMCP CallToolResult into a plain dict."""
    # fastmcp >= 2.3 exposes deserialised output directly.
    for attr in ("data", "structured_content"):
        payload = getattr(result, attr, None)
        if isinstance(payload, dict):
            # FastMCP wraps non-dict returns under "result"; unwrap dict payloads.
            return payload.get("result", payload) if set(payload) == {"result"} else payload

    content = getattr(result, "content", result)
    if isinstance(content, list) and content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    if isinstance(content, dict):
        return content
    return {"text": str(content)}


class MCPToolBridge:
    """Blocking facade over an async FastMCP client session."""

    def __init__(self, url: Optional[str] = None, connect_timeout_s: float = 15.0) -> None:
        self.url = url or SETTINGS.mcp_url
        self.connect_timeout_s = connect_timeout_s
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client = None
        self._ctx = None
        self.connected = False
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """Spin up the loop thread and open the MCP session. Returns success."""
        if self.connected:
            return True
        try:
            from fastmcp import Client
        except ImportError as exc:  # pragma: no cover
            self.last_error = f"fastmcp not installed: {exc}"
            return False

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-bridge", daemon=True
        )
        self._thread.start()

        async def _open() -> None:
            self._client = Client(self.url)
            self._ctx = await self._client.__aenter__()
            await self._ctx.list_tools()  # handshake sanity check

        try:
            self._submit(_open(), timeout=self.connect_timeout_s)
            self.connected = True
        except Exception as exc:
            self.last_error = str(exc)
            self.connected = False
        return self.connected

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.set_exception_handler(self._on_loop_exception)
        self._loop.run_forever()

    @staticmethod
    def _on_loop_exception(loop, context) -> None:
        """Swallow benign transport teardown noise.

        On Windows the proactor event loop logs a full traceback for
        ConnectionResetError (WinError 10054) when a keep-alive HTTP connection
        is recycled. It is harmless - the session reconnects transparently - but
        it floods the control-loop output, so drop it and surface everything else.
        """
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        loop.default_exception_handler(context)

    def _submit(self, coro, timeout: float):
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    # ------------------------------------------------------------------ #
    def call(self, name: str, arguments: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
        """Invoke an MCP tool. Falls back to in-process dispatch on any failure."""
        if not self.connected:
            return call_tool_direct(name, arguments)

        async def _call():
            return await self._ctx.call_tool(name, arguments or {})

        try:
            return _coerce_result(self._submit(_call(), timeout=timeout))
        except Exception as exc:
            self.last_error = f"{name}: {exc}"
            # One transport hiccup must not stall the building.
            return call_tool_direct(name, arguments)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._loop is None:
            return

        async def _close() -> None:
            if self._client is not None:
                await self._client.__aexit__(None, None, None)

        try:
            if self.connected:
                self._submit(_close(), timeout=5.0)
        except Exception:
            pass
        finally:
            self.connected = False
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=3.0)


class DirectToolBridge:
    """Zero-latency stand-in with the same surface as `MCPToolBridge`."""

    connected = True
    last_error = None

    def start(self) -> bool:
        return True

    def call(self, name: str, arguments: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
        return call_tool_direct(name, arguments)

    def close(self) -> None:
        return None


def build_bridge(transport: Optional[str] = None):
    """Factory honouring ECOLOOP_TOOL_TRANSPORT (`mcp` or `direct`)."""
    mode = (transport or SETTINGS.tool_transport or "mcp").lower()
    if mode == "direct":
        return DirectToolBridge()

    from eco_loop import mcp_tools

    if not mcp_tools.start_server_thread():
        print("[mcp] HTTP server unavailable - falling back to direct dispatch")
        return DirectToolBridge()

    bridge = MCPToolBridge()
    if not bridge.start():
        print(f"[mcp] session failed ({bridge.last_error}) - falling back to direct dispatch")
        return DirectToolBridge()
    print(f"[mcp] session established at {bridge.url}")
    return bridge
