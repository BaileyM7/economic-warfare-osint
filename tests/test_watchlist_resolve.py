"""Watchlist entity resolution — Sayari integration (Mike feedback #2).

Adding an entity to the Risk Feed should resolve it via Sayari (strong on
non-US / offshore / shell entities), not just the fuzzy sanctions search. These
tests pin that a confident Sayari hit drives the suggestion, and that the flow
degrades gracefully when Sayari is unavailable.
"""

from __future__ import annotations

import src.routers.watchlist as wl


class _FakeSayariEntity:
    label = "Nuctech Company Limited"
    entity_id = "abc123def456"
    type = "company"
    country = "CHN"
    sanctioned = True
    pep = False


class _FakeSayariResult:
    entities = [_FakeSayariEntity()]


def _stub_non_sayari_resolvers(monkeypatch):
    """Make OFAC/CSL/GDELT return nothing (no network in tests) so the Sayari
    path is exercised in isolation."""

    class _FakeOFAC:
        async def search(self, name):
            return []

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(wl, "OFACClient", _FakeOFAC)
    monkeypatch.setattr(wl, "search_csl", _empty)
    monkeypatch.setattr(wl, "gdelt_doc_search", _empty)


def test_resolve_uses_sayari_canonical_name(app_client, auth_headers, monkeypatch):
    class _FakeSayariClient:
        async def resolve(self, name, limit=3, entity_type=None):
            return _FakeSayariResult()

    monkeypatch.setattr(
        "src.tools.sayari.rest_client.get_sayari_client", lambda: _FakeSayariClient()
    )
    _stub_non_sayari_resolvers(monkeypatch)

    r = app_client.post("/api/watchlist/resolve", json={"name": "Nuctech"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is True
    # Sayari's canonical label replaces the user's typed name...
    assert body["suggestion"]["label"] == "Nuctech Company Limited"
    # ...and a sanctioned company lands in the company_sanctions lane.
    assert body["suggestion"]["category"] == "company_sanctions"
    assert body["confidence"] == "high"
    # Sayari evidence is surfaced (with entity_id + risk flags).
    sayari_ev = next(e for e in body["evidence"] if e["kind"] == "sayari")
    assert sayari_ev["entity_id"] == "abc123def456"
    assert sayari_ev["sanctioned"] is True
    assert sayari_ev["strong_match"] is True


def test_resolve_degrades_gracefully_when_sayari_unavailable(app_client, auth_headers, monkeypatch):
    """Sayari creds absent / API error must not break resolution — it just
    falls through to the existing signals (here: nothing → unresolved)."""

    def _boom():
        raise RuntimeError("no Sayari credentials")

    monkeypatch.setattr("src.tools.sayari.rest_client.get_sayari_client", _boom)
    _stub_non_sayari_resolvers(monkeypatch)

    r = app_client.post(
        "/api/watchlist/resolve", json={"name": "Obscure Shell Co"}, headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    # No crash; no Sayari evidence; still returns a trackable suggestion.
    assert "suggestion" in body
    assert not any(e.get("kind") == "sayari" for e in body.get("evidence", []))


def test_resolve_skips_sayari_override_on_weak_match(app_client, auth_headers, monkeypatch):
    """A Sayari result whose label does NOT contain the user's tokens must not
    hijack the suggestion (guards against a wildly-off top result)."""

    class _OffEntity:
        label = "Completely Different Corp"
        entity_id = "zzz"
        type = "company"
        country = "USA"
        sanctioned = False
        pep = False

    class _OffResult:
        entities = [_OffEntity()]

    class _FakeSayariClient:
        async def resolve(self, name, limit=3, entity_type=None):
            return _OffResult()

    monkeypatch.setattr(
        "src.tools.sayari.rest_client.get_sayari_client", lambda: _FakeSayariClient()
    )
    _stub_non_sayari_resolvers(monkeypatch)

    r = app_client.post("/api/watchlist/resolve", json={"name": "Nuctech"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # Sayari evidence is recorded (strong_match False), but it did NOT override
    # the suggestion label — the weak match is not treated as canonical.
    sayari_ev = next((e for e in body["evidence"] if e["kind"] == "sayari"), None)
    assert sayari_ev is not None and sayari_ev["strong_match"] is False
    assert body["suggestion"]["label"] != "Completely Different Corp"
