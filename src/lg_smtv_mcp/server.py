"""MCP server, ASGI lifespan management, and LG webOS TV tool definitions."""

from mcp.server.mcpserver import MCPServer

from lg_smtv_mcp.auth import ASGIApp
from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.tv_core import TvManager


def create_mcp_server(config: TvConfig, manager: TvManager | None = None) -> MCPServer:
    """Create and configure the MCPServer instance with TV tools and lifespan."""
    raise NotImplementedError


def create_http_app(config: TvConfig, manager: TvManager | None = None) -> ASGIApp:
    """Create the Bearer-authenticated Streamable HTTP ASGI application."""
    raise NotImplementedError
