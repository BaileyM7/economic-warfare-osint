# Smoke Test Results — 2026-03-29

All tests run against `http://localhost:8000` with server started via:

```powershell
conda run -n econ312 uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
```

---

## Endpoint 1: POST /api/sanctions-impact `{"ticker":"SMCI"}`

**Status: PASS**

```json
{
  "target": {
    "ticker": "SMCI",
    "name": "Super Micro Computer, Inc.",
    "sector": "Technology",
    "industry": "Computer Hardware",
    "country": "United States",
    "market_cap": 13192587264.0,
    "current_price": 21.97,
    "change_pct": -1.0806,
    "sanctions_status": { "is_sanctioned": false, "lists": [], "programs": [], "csl_matches": [] }
  },
  "comparables": [...11 entries...],
  "projection": { "mean": [...121 days...], "upper": [...], "lower": [...] }
}
```

**Notes:**
- Comparables set is now expanded and includes non-China archetypes (e.g. `NVDA` with `sanction_type: us_export_control`).
- UTF-8 mojibake still present in some descriptions (cosmetic).

---

## Endpoint 2: POST /api/person-profile `{"name":"Viktor Vekselberg"}`

**Status: PASS**

```json
{
  "name": "Viktor Vekselberg",
  "is_sanctioned": true,
  "sanction_programs": ["UKRAINE-EO13662] [RUSSIA-EO14024"],
  "aliases": [],
  "nationality": null,
  "dob": null,
  "affiliations": [],
  "offshore_connections": [],
  "recent_events": [...2 events...],
  "graph": { "nodes": [1 person node], "edges": [] },
  "sources": ["OpenSanctions", "OFAC SDN", "OpenCorporates", "ICIJ Offshore Leaks", "GDELT"]
}
```

**Notes:**
- OFAC SDN match now correctly flags Vekselberg as sanctioned.
- OpenSanctions public API appears to require an API key (calls may return empty), so OFAC is currently the reliable sanctions source in this environment.

---

## Endpoint 3: POST /api/sector-analysis `{"sector":"semiconductor"}`

**Status: PASS with false positives**

```json
{
  "sector": "semiconductor",
  "sector_key": "semiconductor",
  "company_count": 8,
  "sanctioned_count": 4,
  "companies": [
    { "name": "TSMC", "is_sanctioned": false },
    { "name": "Samsung Electronics", "is_sanctioned": true, "sanction_names": ["SAMSUN"] },
    { "name": "ASML", "is_sanctioned": false },
    { "name": "Nvidia", "is_sanctioned": false },
    { "name": "Intel", "is_sanctioned": true, "sanction_names": ["IRANIAN MINISTRY OF INTELLIGENCE AND SECURITY", "MAIN INTELLIGENCE DIRECTORATE"] },
    { "name": "SMIC", "is_sanctioned": true },
    { "name": "Micron", "is_sanctioned": false },
    { "name": "SK Hynix", "is_sanctioned": false }
  ]
}
```

**Notes:**
- False positives still present due to OFAC name collisions (e.g., Samsung/\"SAMSUN\", Intel collisions).
- Sector registry no longer silently falls back to semiconductor for unknown sectors (now resolves/returns 422).

---

## Endpoint 4: POST /api/vessel-track `{"query":"Lana"}`

**Status: PARTIAL — identity improved, sanctions matching still noisy**

```json
{
  "vessel": { "name": "LANA", "imo": "1012237", "mmsi": "319111800", "flag": "KY", "vessel_type": "Yacht", "source": "fixture" },
  "is_sanctioned": true,
  "sanctions_matches": [
    { "name": "GONZALEZ QUIRARTE, Eduardo", "score": 1.0, "programs": ["SDNTK"] },
    { "name": "DOCKRAT, Farhad Ahmed", "score": 1.0, "programs": ["SDGT"] },
    ...several more person/entity name collisions on "Lana"...
  ],
  "route_history": [],
  "graph": { "nodes": [...], "edges": [...] }
}
```

**Notes:**
- Vessel identity now resolves to the oligarch yacht `LANA` with IMO `1012237` via fixture fallback.
- `sanctions_matches` is still dominated by name-collision hits on the string \"Lana\" (needs vessel-specific sanctions matching).

---

## Endpoint 5: POST /api/analyze/sync `{"query":"What happens if we sanction Huawei?"}`

**Status: PASS — full orchestrator working**

```json
{
  "query": { "raw_query": "...", "scenario_type": "sanction_impact" },
  "executive_summary": "Sanctioning Huawei would severely disrupt global 5G infrastructure...",
  "findings": [
    { "category": "Current Sanctions Status", "confidence": "HIGH", "finding": "..." },
    { "category": "Corporate Structure Vulnerability", "confidence": "MEDIUM", "finding": "..." },
    { "category": "Supply Chain Impact", "confidence": "HIGH", "finding": "..." },
    { "category": "Technology Sector Impact", "confidence": "HIGH", "finding": "..." },
    { "category": "Economic Impact on China", ... }
  ],
  "friendly_fire": [...],
  "entity_graph": { "nodes": [...], "edges": [...] }
}
```

**Notes:**
- Orchestrator pipeline works end-to-end after escaping curly braces in the decomposition prompt template.
- Observed latency: ~80-90s on this run (tool + LLM dependent).

---

## Summary Table

| Endpoint | Status | Key Gap |
|----------|--------|---------|
| /api/sanctions-impact | PASS | Minor encoding mojibake in descriptions |
| /api/person-profile | PASS | OpenSanctions keyless lookups may be unavailable; rely on OFAC |
| /api/sector-analysis | PASS | False positives on OFAC name matching |
| /api/vessel-track | PARTIAL | Sanctions matches still noisy (name collisions), route_history empty without live AIS |
| /api/analyze/sync | PASS | Working end-to-end |
