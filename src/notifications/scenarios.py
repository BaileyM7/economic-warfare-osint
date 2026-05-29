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
        "headline": "If COSCO shipping routes get blocked, Long Beach container costs jump 18%",
        "summary": (
            "If U.S. sanctions force shipments to reroute, West-Coast container "
            "traffic disrupts for about a week. Consumer-electronics importers "
            "with no backup option in Vietnam are most exposed. Our model puts "
            "the price impact at +18% on Long Beach inbound freight."
        ),
    },
    {
        "headline": "If oil producers cut another million barrels a day, U.S. gas hits $3.80",
        "summary": (
            "Brent crude (the global oil benchmark) sits at $95 per barrel. If "
            "OPEC+ — the major oil-producing countries plus Russia — cuts another "
            "1 million barrels per day, U.S. gasoline reaches a $3.80 national "
            "average within 4 weeks. Diesel-dependent industries (trucking, "
            "logistics) feel it first; consumer goods companies follow."
        ),
    },
    {
        "headline": "Taiwan Strait stress test: economic damage starts within 4 days of any blockade",
        "summary": (
            "We modeled a 72-hour Chinese blockade of Taiwan. Semiconductor "
            "supply chains start breaking inside 4 days; the U.S. dollar moves "
            "immediately. Companies most affected in your watchlist would be "
            "those exposed to Taiwanese chip manufacturing and the equipment "
            "that supplies it."
        ),
    },
    {
        "headline": "Russia sanctions evasion: how a price-cap workaround triggers a U.S. dollar squeeze",
        "summary": (
            "Three shell companies (entities set up to hide the real owner) have "
            "been moving Russian oil above the West's $60-per-barrel price ceiling. "
            "If the U.S. sanctions the banks helping them, U.S.-dollar transactions "
            "for those banks halt for about 48 hours — long enough to ripple into "
            "global trade settlement."
        ),
    },
    {
        "headline": "China's rare-earth export limits: 6 months of price spikes, 2 years to recover",
        "summary": (
            "China tightened its rare-earth export licensing again last quarter. "
            "Rare earths are the specialty metals in EV motors, wind turbines, and "
            "missile guidance — China controls roughly 70% of global supply. Our "
            "model shows 6 months of sharp price spikes before alternative "
            "production (Australia, Greenland) closes the gap; full supply "
            "rebalance takes about 2 years."
        ),
    },
]


def select_scenario_for_week(week_iso: str) -> dict[str, str]:
    """Deterministically pick one scenario per ISO week (e.g. '2026-W21')."""
    week_num = int(week_iso.split("-W")[1]) if "-W" in week_iso else 0
    return SCENARIOS[week_num % len(SCENARIOS)]
