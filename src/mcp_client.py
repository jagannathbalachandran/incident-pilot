"""
mcp_client.py

Synchronous wrapper around the MCP (Model Context Protocol) SDK, which is
async-native, so it can be called from the rest of this codebase (ChatGroq,
Gradio callbacks), which is not.

Spawns ``mcp_server/server.py`` once as a stdio subprocess and keeps a single
background asyncio event loop alive for the lifetime of the process. Every
``call_tool()`` is dispatched onto that loop from whatever thread calls it
and blocks for the result -- there is exactly one live MCP session, reused
across every query.
"""

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).parent


class MCPClient:
    """Owns one long-lived MCP stdio session, exposed synchronously.

    ``start()`` must be called once before ``call_tool()``/``list_tools()``.
    ``close()`` shuts the background loop and the server subprocess down.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Optional[ClientSession] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._start_error: Optional[BaseException] = None

    def start(self, timeout: float = 15.0) -> None:
        """Start the background loop, spawn the MCP server, open the session."""
        if self._thread is not None:
            return  # already started

        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-client-loop", daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=timeout):
            raise TimeoutError("MCP server did not become ready in time")
        if self._start_error is not None:
            raise self._start_error

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - surface to start()
            self._start_error = exc
            self._ready.set()
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        # Forward the full parent environment to the server subprocess.
        # StdioServerParameters(env=None) does NOT inherit the parent env --
        # the MCP SDK's get_default_environment() passes only a scrubbed
        # allowlist (HOME, PATH), which would drop PROMETHEUS_URL / LOKI_URL /
        # GROQ_API_KEY and silently send every telemetry query to localhost.
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            cwd=str(SRC_DIR),
            env=dict(os.environ),
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    logger.info("MCP session established (server: mcp_server.server)")
                    self._ready.set()
                    # Keep the session open until close() is requested.
                    while not self._stop.is_set():
                        await asyncio.sleep(0.1)
        finally:
            self._session = None

    def call_tool(self, name: str, arguments: dict) -> dict[str, Any]:
        """Call an MCP tool by name and return its result as a dict.

        Blocks the calling thread until the tool call completes.
        """
        if self._loop is None or self._session is None:
            raise RuntimeError("MCPClient.start() must be called before call_tool()")

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop,
        )
        result = future.result()

        if result.isError:
            text = "".join(
                getattr(block, "text", str(block)) for block in result.content
            )
            raise RuntimeError(f"MCP tool '{name}' returned an error: {text}")

        if result.structuredContent is not None:
            return result.structuredContent

        # Fall back to parsing the JSON text FastMCP serializes dict returns to.
        text_blocks = [
            block.text for block in result.content if getattr(block, "text", None)
        ]
        if not text_blocks:
            return {}
        return json.loads(text_blocks[0])

    def list_tools(self) -> list[str]:
        if self._loop is None or self._session is None:
            raise RuntimeError("MCPClient.start() must be called before list_tools()")
        future = asyncio.run_coroutine_threadsafe(
            self._session.list_tools(), self._loop,
        )
        return [t.name for t in future.result().tools]

    def close(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._thread = None
