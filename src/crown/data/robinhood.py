"""Positive read-tool boundary for a future authorized Robinhood MCP transport.

This module does not obtain OAuth credentials or connect a server. A connected
transport must supply the discovered catalog; schemas are pinned by hash. Unknown
tools and all mutations fail before dispatch, including similarly named tools.
"""
from crown.research.models import digest


READ_TOOLS = frozenset({
    "get_equity_news", "get_equity_quotes", "get_equity_historicals",
    "get_earnings_calendar", "get_earnings_results", "get_index_quotes",
    "get_index_historicals", "get_option_chains", "get_option_quotes",
})


class ResearchTools:
    def __init__(self, catalog, approved_schema_hashes, dispatch):
        # No implicit approval of changed tool schemas at reconnect.
        self._schemas = {}
        for tool in catalog:
            name = tool["name"]
            if name in READ_TOOLS and approved_schema_hashes.get(name) == digest(tool):
                self._schemas[name] = digest(tool)
        self._dispatch = dispatch

    @property
    def available(self):
        return tuple(sorted(self._schemas))

    async def call(self, name, arguments):
        if name not in self._schemas:
            raise RuntimeError("Robinhood research tool not allowlisted with an approved schema")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a structured object")
        return await self._dispatch(name, arguments)
