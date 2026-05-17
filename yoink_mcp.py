"""Yoink MCP stdio entry point.

Run with:

    python yoink_mcp.py

MCP clients launch this process and speak JSON-RPC over stdin/stdout. Keep
stdout reserved for the protocol; server.py logging is redirected to stderr
while importing the backend.
"""

from __future__ import annotations

import sys


try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "Yoink MCP requires the official MCP Python SDK. "
        "Install with: python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)


# server.py configures a stdout log handler at import time. MCP stdio uses
# stdout as the JSON-RPC transport, so bind that log handler to stderr instead.
_stdout = sys.stdout
try:
    sys.stdout = sys.stderr
    import server  # noqa: E402
finally:
    sys.stdout = _stdout

import yoink_mcp_tools  # noqa: E402


yoink_mcp_tools.bind_backend(server)

mcp = FastMCP(
    "yoink",
    instructions=(
        "Yoink turns YouTube videos and playlists into local AI-ready corpora. "
        "Use the tools to extract, search, inspect, and analyze saved yoinks."
    ),
)


@mcp.tool(
    name="yoink_video",
    description="Extract a single YouTube video into a Yoink corpus.",
)
def yoink_video(url: str, interval: int = 30) -> dict:
    return yoink_mcp_tools.call_tool("yoink_video", {"url": url, "interval": interval})


@mcp.tool(
    name="yoink_playlist",
    description="Start asynchronous extraction for a YouTube playlist.",
)
def yoink_playlist(url: str, interval: int = 30) -> dict:
    return yoink_mcp_tools.call_tool("yoink_playlist", {"url": url, "interval": interval})


@mcp.tool(
    name="get_job_status",
    description="Return the full status object for an async Yoink job.",
)
def get_job_status(job_id: str) -> dict:
    return yoink_mcp_tools.call_tool("get_job_status", {"job_id": job_id})


@mcp.tool(
    name="cancel_job",
    description="Cancel an async Yoink job and leave partial outputs on disk.",
)
def cancel_job(job_id: str) -> dict:
    return yoink_mcp_tools.call_tool("cancel_job", {"job_id": job_id})


@mcp.tool(name="list_recent_yoinks", description="List recent saved Yoink corpora.")
def list_recent_yoinks(limit: int = 20) -> dict:
    return yoink_mcp_tools.call_tool("list_recent_yoinks", {"limit": limit})


@mcp.tool(
    name="search_yoinks",
    description="Keyword search across saved Yoink markdown corpora.",
)
def search_yoinks(query: str, limit: int = 10) -> dict:
    return yoink_mcp_tools.call_tool("search_yoinks", {"query": query, "limit": limit})


@mcp.tool(
    name="get_yoink_corpus",
    description="Return the full markdown corpus for a saved yoink by slug.",
)
def get_yoink_corpus(slug: str) -> dict:
    return yoink_mcp_tools.call_tool("get_yoink_corpus", {"slug": slug})


@mcp.tool(
    name="analyze_comments",
    description=(
        "Run Comment Intelligence on an existing yoink using the configured "
        "Anthropic key."
    ),
)
def analyze_comments(slug: str) -> dict:
    return yoink_mcp_tools.call_tool("analyze_comments", {"slug": slug})


@mcp.tool(
    name="classify_hook",
    description="Classify the hook type for an existing yoink.",
)
def classify_hook(slug: str) -> dict:
    return yoink_mcp_tools.call_tool("classify_hook", {"slug": slug})


@mcp.tool(
    name="get_taxonomy",
    description=(
        "Return captured Hook Type taxonomy rows, optionally "
        "filtered by channel and hook_type."
    ),
)
def get_taxonomy(
    channel: str | None = None,
    hook_type: str | None = None,
    limit: int = 50,
) -> dict:
    return yoink_mcp_tools.call_tool(
        "get_taxonomy",
        {"channel": channel, "hook_type": hook_type, "limit": limit},
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")

