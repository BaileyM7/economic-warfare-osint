"""News tool (Phase 3 C) — recent GDELT headlines. Mocked (no network)."""

from __future__ import annotations

import src.tools.news.client as news_client
from src.orchestrator.tool_registry import ToolRegistry
from src.tools.news.server import search_recent_news


async def test_search_recent_news_envelope(monkeypatch):
    async def fake_fetch(url, params=None, **kw):
        return {
            "articles": [
                {
                    "title": "Sanctions hit chipmaker",
                    "url": "https://ex.com/a",
                    "domain": "ex.com",
                    "seendate": "20260618T120000Z",
                    "language": "English",
                },
                {"title": "", "url": "https://ex.com/b"},  # dropped: no title
            ]
        }

    monkeypatch.setattr(news_client, "fetch_json", fake_fetch)
    monkeypatch.setattr(news_client, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(news_client, "set_cached", lambda *a, **k: None)

    resp = await search_recent_news("chipmaker sanctions", days=7)
    assert resp["data"]["count"] == 1  # the title-less article was dropped
    art = resp["data"]["articles"][0]
    assert art["title"] == "Sanctions hit chipmaker"
    assert art["url"] == "https://ex.com/a"
    assert art["source"] == "ex.com"
    assert resp["sources"][0]["name"] == "GDELT 2.0 Doc API"
    assert resp["errors"] == []


async def test_registry_registers_news_agent():
    r = ToolRegistry()
    await r._ensure_loaded()
    assert "search_recent_news" in r.list_tools()
    assert r.tool_domain("search_recent_news") == "news"
