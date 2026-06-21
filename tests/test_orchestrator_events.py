"""Structured agent-swarm events from the orchestrator (Phase 3 A1).

These guard the contract the live "swarm of AI agents" UI consumes off
/api/analyze/{id}.events: a plan event + a running/done event per tool call,
each tagged with its data domain, a result chip, and a duration.
"""

from __future__ import annotations

import asyncio

from src.orchestrator.main import (
    Orchestrator,
    _cap_plan,
    _coerce_plan_list,
    _partial_field,
    _collect_identifiers,
    _extract_findings,
    _has_unresolved,
    _resolve_params,
    _step_tool_names,
    _summarize_tool_result,
)


def test_opaque_id_sources_are_filtered():
    import re

    from src.orchestrator.main import _looks_like_opaque_id, _merge_sources

    # Opaque machine IDs → dropped
    assert _looks_like_opaque_id("a8c6ee1cd4dfc952105ee8c0e4836f08")  # 32-char hex hash
    # Real source names → kept
    assert not _looks_like_opaque_id("OFAC SDN")
    assert not _looks_like_opaque_id("OpenCorporates")
    assert not _looks_like_opaque_id("GDELT 2.0 Doc API")
    # End-to-end: tool + LLM sources with hashes mixed in → only real names survive.
    tr = {
        "step_1": {"sources": [{"name": "a8c6ee1cd4dfc952105ee8c0e4836f08"}, {"name": "OFAC SDN"}]}
    }
    names = {
        s.name for s in _merge_sources(tr, ["24ca9e1480ab586a153d3ae12a7b83a0", "OpenSanctions"])
    }
    assert "OFAC SDN" in names and "OpenSanctions" in names
    assert not any(re.fullmatch(r"[0-9a-fA-F]{16,}", n) for n in names)


def test_partial_field_decodes_incomplete_streaming_json():
    # Mid-stream: executive_summary open, not yet closed → return what's there so far.
    buf = (
        '{"scenario_type": "sanction_impact", "executive_summary": "Sanctioning Fujian Jinhua would'
    )
    assert _partial_field(buf, "executive_summary") == "Sanctioning Fujian Jinhua would"
    # Completed field with an escaped quote → decoded and stopped at the real close.
    done = '{"executive_summary": "A \\"hard\\" hit.", "findings": []}'
    assert _partial_field(done, "executive_summary") == 'A "hard" hit.'
    # Field not present yet → None.
    assert _partial_field('{"scenario_type": "x"', "executive_summary") is None


def test_dependency_resolution_substitutes_prior_identifiers():
    prior = {
        "step_1": {"results": {"sayari_resolve": {"data": {"entities": [{"entity_id": "ABC123"}]}}}}
    }
    ids = _collect_identifiers(prior)
    assert ids.get("entity_id") == "ABC123"
    resolved = _resolve_params({"sayari_id": "{{fujian_jinhua_sayari_id}}", "limit": 50}, ids)
    assert resolved["sayari_id"] == "ABC123"  # placeholder filled from prior step
    assert resolved["limit"] == 50  # non-placeholder untouched
    assert not _has_unresolved(resolved)
    # No prior identifier → stays unresolved so the caller skips the doomed call.
    assert _has_unresolved(_resolve_params({"id": "{{missing}}"}, {}))


def test_extract_findings_surfaces_rows_sources_and_errors():
    # List-of-rows under a known key → labelled items + qualifier + sources/conf.
    res = {
        "data": {
            "articles": [
                {"title": "US tightens chip controls", "source": "reuters.com"},
                {"title": "Allies weigh response"},
            ]
        },
        "confidence": "MEDIUM",
        "sources": [{"name": "GDELT 2.0 Doc API"}],
    }
    out = _extract_findings(res)
    assert out["items"][0] == "US tightens chip controls — reuters.com"
    assert out["items"][1] == "Allies weigh response"
    assert out["confidence"] == "medium"
    assert out["sources"] == ["GDELT 2.0 Doc API"]
    # Error result → friendly message, never the raw URL/placeholder/HTTP text.
    sayari_404 = (
        "Client error '404 Not Found' for url "
        "'https://api.sayari.com/v1/ubo/%7B%7Bfujian_jinhua_sayari_id%7D%7D'"
    )
    out404 = _extract_findings({"error": sayari_404})
    assert out404["error"] == "No matching records"
    assert "http" not in out404["error"].lower() and "%7b" not in out404["error"].lower()
    assert _extract_findings({"error": "429 Too Many Requests"})["error"] == "Source busy — skipped"
    # Scalar dict → key:value rows.
    assert "lei: 5493" in " ".join(_extract_findings({"data": {"lei": "5493..."}})["items"])


def test_coerce_plan_list_normalizes_llm_shapes():
    step = {"step": 1, "description": "d", "tools": ["a('x')"]}
    # Bare list (the happy path) passes through, dropping stray non-dicts.
    assert _coerce_plan_list([step, "junk"]) == [step]
    # Object-wrapped variants the LLM intermittently emits → unwrapped to a list.
    assert _coerce_plan_list({"steps": [step]}) == [step]
    assert _coerce_plan_list({"plan": [step]}) == [step]
    # A lone step object → wrapped in a list (not iterated as dict keys).
    assert _coerce_plan_list(step) == [step]
    # Unusable shapes → empty, so the caller falls back to a basic plan.
    assert _coerce_plan_list("nope") == []
    assert _coerce_plan_list({"unrelated": 1}) == []


def test_cap_plan_trims_fanout_preserving_structure():
    plan = [
        {"step": 1, "description": "a", "tools": [{"name": f"t{i}"} for i in range(10)]},
        {
            "step": 2,
            "description": "b",
            "depends_on": [1],
            "tools": [{"name": f"u{i}"} for i in range(10)],
        },
        {"step": 3, "description": "c", "tools": [{"name": f"v{i}"} for i in range(10)]},
    ]
    capped = _cap_plan(plan, max_total=10, max_per_step=4)
    # No step exceeds the per-step cap...
    assert all(len(s["tools"]) <= 4 for s in capped)
    # ...and the total across the plan is within the global budget.
    assert sum(len(s["tools"]) for s in capped) <= 10
    # Step identity + dependencies are preserved (only surplus tools dropped).
    assert [s["step"] for s in capped] == [1, 2, 3]
    assert capped[1]["depends_on"] == [1]
    # A non-positive budget is a no-op (feature disabled).
    assert _cap_plan(plan, max_total=0) == plan


class _SlowRegistry:
    """Records max concurrent in-flight calls so a test can prove parallelism."""

    def __init__(self) -> None:
        self.inflight = 0
        self.max_inflight = 0

    def tool_domain(self, name: str) -> str:
        return "market"

    async def call_tool(self, name: str, params: dict):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        await asyncio.sleep(0.02)
        self.inflight -= 1
        return {"data": [1], "confidence": "HIGH"}


async def test_execute_step_runs_tools_concurrently():
    orch = Orchestrator.__new__(Orchestrator)
    reg = _SlowRegistry()
    orch.tool_registry = reg
    events: list[dict] = []
    step = {
        "step": 1,
        "description": "fan out",
        "tools": [{"name": f"t{i}"} for i in range(4)],
    }
    sem = asyncio.Semaphore(8)
    await orch._execute_step(step, {}, on_event=events.append, step_num=1, sem=sem)

    # All four tools completed and each emitted a done event.
    done = [e for e in events if e["status"] == "done"]
    assert len(done) == 4
    # They overlapped — serial execution would cap max_inflight at 1.
    assert reg.max_inflight > 1


async def test_execute_step_respects_semaphore_limit():
    orch = Orchestrator.__new__(Orchestrator)
    reg = _SlowRegistry()
    orch.tool_registry = reg
    step = {"step": 1, "description": "x", "tools": [{"name": f"t{i}"} for i in range(6)]}
    sem = asyncio.Semaphore(2)
    await orch._execute_step(step, {}, on_event=lambda e: None, step_num=1, sem=sem)
    # The shared semaphore caps concurrency at 2 even with 6 tools queued.
    assert reg.max_inflight <= 2


def test_step_tool_names_handles_dict_and_string_calls():
    step = {"tools": [{"tool": "a"}, {"name": "b"}, "c('x')"]}
    assert _step_tool_names(step) == ["a", "b", "c"]
    assert _step_tool_names({}) == []


def test_summarize_tool_result():
    assert _summarize_tool_result({"data": [1, 2, 3], "confidence": "HIGH"}) == "3 results, high"
    assert _summarize_tool_result({"data": {"a": 1}}) == "1 result"
    assert _summarize_tool_result({"error": "boom"}) == "error"
    assert _summarize_tool_result({"data": []}) == "ok"
    assert _summarize_tool_result("nope") == "ok"


class _FakeRegistry:
    def tool_domain(self, name: str) -> str:
        return {"check_sanctions_status": "sanctions"}.get(name, "unknown")

    async def call_tool(self, name: str, params: dict):
        return {"data": [1, 2, 3], "confidence": "HIGH"}


async def test_execute_step_emits_running_then_done_per_tool():
    # __new__ bypasses Orchestrator.__init__ (which requires ANTHROPIC_API_KEY).
    orch = Orchestrator.__new__(Orchestrator)
    orch.tool_registry = _FakeRegistry()

    events: list[dict] = []
    step = {
        "step": 1,
        "description": "screen the target",
        "tools": [{"tool": "check_sanctions_status", "params": {}}],
    }
    await orch._execute_step(step, {}, on_event=events.append, step_num=1)

    pairs = [(e["name"], e["status"]) for e in events]
    assert ("check_sanctions_status", "running") in pairs
    assert ("check_sanctions_status", "done") in pairs

    running = next(e for e in events if e["status"] == "running")
    assert running["task"] == "screen the target"  # task is on the running event
    done = next(e for e in events if e["status"] == "done")
    assert done["domain"] == "sanctions"
    assert done["summary"] == "3 results, high"
    assert isinstance(done["ms"], int)


class _BoomRegistry:
    def tool_domain(self, name: str) -> str:
        return "market"

    async def call_tool(self, name: str, params: dict):
        raise RuntimeError("upstream 500")


async def test_execute_step_marks_tool_error_without_raising():
    orch = Orchestrator.__new__(Orchestrator)
    orch.tool_registry = _BoomRegistry()
    events: list[dict] = []
    step = {"step": 2, "description": "quote", "tools": [{"tool": "get_stock_profile"}]}
    # Must not raise — a failing agent is reported, not fatal.
    await orch._execute_step(step, {}, on_event=events.append, step_num=2)
    done = next(e for e in events if e["status"] == "error")
    assert done["name"] == "get_stock_profile"
    assert done["summary"] == "error"
