"""Country-agent and arbiter nodes for the LangGraph simulation.

NOTE: this package intentionally does NOT eagerly re-export submodules.
``world.py`` (the simulation core) needs to import ``leader_profile`` from
this package, but eagerly importing ``arbiter`` here would create a cycle
(arbiter → escalation_ladder → world → leader_profile → here → arbiter).
All call-sites import the submodules directly (e.g.
``from wargame_ai.agents.country_agent import CountryAgent``).
"""
