"""Agora Evidence MCP server.

Exposes research *tools* to debater agents: searching sources, fetching
source content, and verifying that a quote actually appears in a source.
Backed by the Wikipedia API so it needs no API key.

Both debaters connect to this same server, so neither side gets
privileged evidence access.

Set AGORA_EVIDENCE_OFFLINE=1 to serve deterministic fixture data instead
of calling Wikipedia (used by the backend test suite and mock mode).
"""

import html
import os
import re

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agora-evidence")

WIKI_API = "https://en.wikipedia.org/w/api.php"
# Wikimedia's API policy requires a User-Agent with contact info —
# a generic UA gets 403 Forbidden.
HEADERS = {
    "User-Agent": "AgoraDebateArena/0.1"
                  " (+https://github.com/MartinSG98/agora-backend)"
}

OFFLINE = os.environ.get("AGORA_EVIDENCE_OFFLINE", "0") == "1"

_FIXTURE_SOURCES = {
    "1001": {
        "title": "Remote work",
        "content": (
            "Remote work is the practice of employees performing their job "
            "outside of a traditional office environment. Studies conducted "
            "between 2020 and 2024 found that remote workers reported higher "
            "job satisfaction on average. Some organisations observed a "
            "decline in spontaneous collaboration after moving fully remote."
        ),
    },
    "1002": {
        "title": "Four-day workweek",
        "content": (
            "A four-day workweek is an arrangement where employees work four "
            "days per week instead of five. Trials in several countries "
            "reported maintained or improved productivity alongside reduced "
            "burnout. Critics note that results vary significantly by industry."
        ),
    },
}


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


async def _wiki_get(params: dict) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        response = await client.get(WIKI_API, params={**params, "format": "json"})
        response.raise_for_status()
        return response.json()


async def _fetch_extract(source_id: str) -> dict | None:
    if OFFLINE:
        fixture = _FIXTURE_SOURCES.get(source_id)
        if fixture is None:
            return None
        return {"source_id": source_id, **fixture}
    data = await _wiki_get(
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "pageids": source_id,
        }
    )
    page = data.get("query", {}).get("pages", {}).get(source_id)
    if page is None or "missing" in page:
        return None
    return {
        "source_id": source_id,
        "title": page.get("title", ""),
        "content": page.get("extract", ""),
    }


@mcp.tool()
async def search_sources(query: str, limit: int = 5) -> dict:
    """Search for encyclopedia sources relevant to a query.

    Returns {"results": [{source_id, title, snippet}, ...]}. Use
    get_source_content with a source_id to read a source before citing it.
    """
    limit = max(1, min(limit, 10))
    if OFFLINE:
        return {
            "results": [
                {
                    "source_id": source_id,
                    "title": fixture["title"],
                    "snippet": fixture["content"][:160],
                }
                for source_id, fixture in list(_FIXTURE_SOURCES.items())[:limit]
            ]
        }
    data = await _wiki_get(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        }
    )
    return {
        "results": [
            {
                "source_id": str(item["pageid"]),
                "title": item["title"],
                "snippet": _strip_html(item.get("snippet", "")),
            }
            for item in data.get("query", {}).get("search", [])
        ]
    }


@mcp.tool()
async def get_source_content(source_id: str, max_chars: int = 4000) -> dict:
    """Fetch the plain-text content of a source by its source_id.

    Content is truncated to max_chars. Cite the source_id when quoting.
    """
    source = await _fetch_extract(source_id)
    if source is None:
        return {"error": f"source {source_id} not found"}
    source["content"] = source["content"][: max(200, max_chars)]
    return source


@mcp.tool()
async def verify_quote(source_id: str, quote: str) -> dict:
    """Check whether a quote is actually supported by a source.

    Returns a verdict: 'supported' (quote appears verbatim),
    'partially_supported' (most of the quote's words appear in the source),
    or 'not_found'.
    """
    source = await _fetch_extract(source_id)
    if source is None:
        return {"source_id": source_id, "verdict": "source_not_found"}
    document = _normalise(source["content"])
    needle = _normalise(quote)
    if not needle:
        return {"source_id": source_id, "verdict": "not_found"}
    if needle in document:
        return {"source_id": source_id, "verdict": "supported"}
    quote_words = set(needle.split())
    doc_words = set(document.split())
    overlap = len(quote_words & doc_words) / len(quote_words)
    if overlap >= 0.8:
        return {
            "source_id": source_id,
            "verdict": "partially_supported",
            "word_overlap": round(overlap, 2),
        }
    return {"source_id": source_id, "verdict": "not_found"}


if __name__ == "__main__":
    mcp.run()
