"""Connection to the Sanity Context MCP endpoint (Knowledge Base mode)."""

from contextlib import AsyncExitStack

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import config

PREFIX = "kb_"   # every knowledge-base tool is exposed to Claude as kb_<name>

# Only these read-only Knowledge Base tools become model capabilities, whatever
# else the endpoint may expose in future.
ALLOWED_TOOLS = {"initial_context", "knowledge_base_read"}


class KnowledgeBase:
    """Async context manager: `async with KnowledgeBase() as kb: kb.tools, await kb.call(...)`."""

    instructions = ("Start with kb_initial_context to see what the knowledge base covers, "
                    "then read the entries you need with kb_knowledge_base_read.")

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        http = await self._stack.enter_async_context(httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {config.secret('org_token')}"},
            timeout=httpx2.Timeout(60.0, read=300.0),
        ))
        read, write = await self._stack.enter_async_context(
            streamable_http_client(config.mcp_url(), http_client=http))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [{
            "name": PREFIX + t.name,
            "description": t.description or "",
            "input_schema": t.input_schema,
        } for t in listed.tools if t.name in ALLOWED_TOOLS]
        missing = ALLOWED_TOOLS - {t.name for t in listed.tools}
        if missing:
            raise RuntimeError(f"MCP endpoint is missing Knowledge Base tools: {sorted(missing)}")
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def call(self, tool_name, arguments):
        """Call a kb_ tool. Returns (text, is_error)."""
        result = await self.session.call_tool(tool_name.removeprefix(PREFIX), arguments)
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        return text, bool(result.is_error)
