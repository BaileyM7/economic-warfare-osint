"""Watchlist entity resolution — Sayari integration (Mike feedback #2).

Adding an entity to the Risk Feed should resolve it via Sayari (strong on
non-US / offshore / shell entities), not just the fuzzy sanctions search. The
gate trusts Sayari's OWN `match_strength` verdict (so a confident non-Latin
canonical name still wins) and never replaces the user's recognizable typed
name with a non-Latin legal name. These tests pin that behavior and the
graceful-degradation path.
"""

from __future__ import annotations

import src.routers.watchlist as wl


def _entity(**kw):
    """Build a stand-in Sayari entity with sensible defaults."""
    defaults = dict(
        label="",
        entity_id="id-x",
        type="company",
        country="CHN",
        sanctioned=False,
        pep=False,
        match_strength=None,
    )
    defaults.update(kw)
    return type("FakeSayariEntity", (), defaults)()


def _patch_sayari(monkeypatch, entity):
    class _FakeSayariClient:
        async def resolve(self, name, limit=3, entity_type=None):
            return type("R", (), {"entities": [entity] if entity else []})()

    monkeypatch.setattr(
        "src.tools.sayari.rest_client.get_sayari_client", lambda: _FakeSayariClient()
    )


def _stub_non_sayari_resolvers(monkeypatch):
    """OFAC/CSL/GDELT return nothing (no network in tests) so the Sayari path is
    exercised in isolation."""

    class _FakeOFAC:
        async def search(self, name):
            return []

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(wl, "OFACClient", _FakeOFAC)
    monkeypatch.setattr(wl, "search_csl", _empty)
    monkeypatch.setattr(wl, "gdelt_doc_search", _empty)


def _resolve(app_client, auth_headers, name):
    r = app_client.post("/api/watchlist/resolve", json={"name": name}, headers=auth_headers)
    assert r.status_code == 200
    return r.json()


def test_resolve_uses_sayari_canonical_name_on_latin_match(app_client, auth_headers, monkeypatch):
    # Latin canonical name containing the user's tokens → use it (high).
    _patch_sayari(
        monkeypatch,
        _entity(
            label="Nuctech Company Limited",
            entity_id="abc",
            sanctioned=True,
            match_strength="strong",
        ),
    )
    _stub_non_sayari_resolvers(monkeypatch)
    body = _resolve(app_client, auth_headers, "Nuctech")
    assert body["resolved"] is True and body["confidence"] == "high"
    assert body["suggestion"]["label"] == "Nuctech Company Limited"
    assert body["suggestion"]["category"] == "company_sanctions"
    ev = next(e for e in body["evidence"] if e["kind"] == "sayari")
    assert ev["entity_id"] == "abc" and ev["strong_match"] is True


def test_resolve_strong_verdict_keeps_typed_name_for_nonlatin(
    app_client, auth_headers, monkeypatch
):
    # Sayari is confident ("strong") but the canonical label is non-Latin → trust
    # the resolution (high) but KEEP the user's recognizable typed name.
    _patch_sayari(
        monkeypatch,
        _entity(
            label="同方威视技术股份有限公司",
            match_strength="strong",
            sanctioned=False,
            type="company",
        ),
    )
    _stub_non_sayari_resolvers(monkeypatch)
    body = _resolve(app_client, auth_headers, "Nuctech")
    assert body["resolved"] is True and body["confidence"] == "high"
    assert body["suggestion"]["label"] == "Nuctech"  # NOT the Chinese legal name
    assert body["suggestion"]["category"] == "markets"  # company, not sanctioned
    ev = next(e for e in body["evidence"] if e["kind"] == "sayari")
    assert ev["match_strength"] == "strong"


def test_resolve_probable_weak_keeps_name_but_categorizes(app_client, auth_headers, monkeypatch):
    # A weak/possible Sayari candidate (non-US entity not on any US list) →
    # resolved MEDIUM, typed name kept, category from Sayari type + sanction flag.
    _patch_sayari(
        monkeypatch,
        _entity(label="某外国公司", match_strength="weak", sanctioned=True, type="company"),
    )
    _stub_non_sayari_resolvers(monkeypatch)
    body = _resolve(app_client, auth_headers, "Sinco Holdings Ltd")
    assert body["resolved"] is True and body["confidence"] == "medium"
    assert body["suggestion"]["label"] == "Sinco Holdings Ltd"
    assert body["suggestion"]["category"] == "company_sanctions"  # Sayari says sanctioned


def test_resolve_weak_never_hijacks_label(app_client, auth_headers, monkeypatch):
    # Even a weak match whose label is unrelated must never become the suggestion
    # label (guards against a wildly-off top result hijacking the user's intent).
    _patch_sayari(
        monkeypatch,
        _entity(label="Completely Different Corp", match_strength="weak", type="company"),
    )
    _stub_non_sayari_resolvers(monkeypatch)
    body = _resolve(app_client, auth_headers, "Nuctech")
    assert body["suggestion"]["label"] != "Completely Different Corp"
    ev = next(e for e in body["evidence"] if e["kind"] == "sayari")
    assert ev["strong_match"] is False


def test_resolve_degrades_gracefully_when_sayari_unavailable(app_client, auth_headers, monkeypatch):
    def _boom():
        raise RuntimeError("no Sayari credentials")

    monkeypatch.setattr("src.tools.sayari.rest_client.get_sayari_client", _boom)
    _stub_non_sayari_resolvers(monkeypatch)
    body = _resolve(app_client, auth_headers, "Obscure Shell Co")
    assert "suggestion" in body
    assert not any(e.get("kind") == "sayari" for e in body.get("evidence", []))
