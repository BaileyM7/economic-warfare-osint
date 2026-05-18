"""Static scenario-spotlight rotation for the weekly digest.

These are hand-written demo scenarios. In a future iteration they could be
generated from real wargame runs (SimEvent table on the Postgres side), but
for the demo this fixed rotation is sufficient.

The rotation is deterministic by ISO week so users see the same scenario
all week and a fresh one each Monday.
"""

from __future__ import annotations

SCENARIOS: list[dict[str, str]] = [
    {
        "headline": "If COSCO routes are blocked, container costs from Long Beach spike 18%",
        "summary": (
            "Our wargame projects a 7-day disruption to West-Coast container traffic "
            "if the latest SDN designation triggers a routing cascade. Largest exposure: "
            "consumer electronics importers with no Vietnam alternative."
        ),
    },
    {
        "headline": "Brent at $95 — what changes if OPEC+ cuts another 1 mbpd",
        "summary": (
            "Scenario modeling shows a +1 mbpd cut at current demand pushes US gasoline "
            "to a $3.80 national average within 4 weeks. Diesel responds faster than "
            "gasoline; consumer discretionary takes the secondary hit."
        ),
    },
    {
        "headline": "Taiwan Strait tabletop: 72-hour kinetic-to-economic transition",
        "summary": (
            "The last wargame run modeled a 72-hour blockade scenario. Semiconductor "
            "supply impact begins inside 96 hours; FX impact is immediate. Watchlist "
            "items in tech and chip-equipment names see the sharpest re-rating."
        ),
    },
    {
        "headline": "Russia oil-price-cap evasion: secondary-sanction trigger paths",
        "summary": (
            "Trading-arm shell entities documented on OpenSanctions have been moving "
            "Urals volume above the $60 cap. Secondary sanctions on associated banks "
            "create a 48-hour USD-clearing disruption per our scenario."
        ),
    },
    {
        "headline": "Rare-earth export controls: 2-year reshoring runway, 6-month price shock",
        "summary": (
            "China's rare-earth export licensing tightened again last quarter. "
            "Scenario modeling shows 6 months of acute price spikes before "
            "alternative supply (Australia, Greenland) clears the demand gap."
        ),
    },
]


def select_scenario_for_week(week_iso: str) -> dict[str, str]:
    """Deterministically pick one scenario per ISO week (e.g. '2026-W21')."""
    week_num = int(week_iso.split("-W")[1]) if "-W" in week_iso else 0
    return SCENARIOS[week_num % len(SCENARIOS)]
