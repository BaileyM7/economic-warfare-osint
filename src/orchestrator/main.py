"""Orchestrator Agent — the 'quarterback' that decomposes questions and synthesizes results.

Usage:
    uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from src.common.config import config
from src.common.llm_provider import get_text_provider
from src.fusion.graph_builder import build_graph_from_results
from src.common.types import (
    AnalystQuery,
    Confidence,
    ImpactAssessment,
    ScenarioType,
    SourceReference,
)
from src.orchestrator.prompts import (
    DECOMPOSITION_MEMORY_BLOCK,
    DECOMPOSITION_PROMPT,
    SYNTHESIS_MEMORY_BLOCK,
    SYNTHESIS_PROMPT,
    SYNTHESIS_SYSTEM_SUPPLEMENT,
    SYSTEM_PROMPT,
)
from src.orchestrator.tool_registry import ToolRegistry


class Orchestrator:
    """Top-level agent that owns the conversation and research plan."""

    def __init__(self) -> None:
        issues = config.validate()
        if issues:
            raise RuntimeError(
                "Config issues: "
                + ", ".join(issues)
                + ". Set missing keys in .env file. See .env.example for reference."
            )

        # Pluggable LLM provider (issue #33): anthropic by default, or a local
        # OpenAI-compatible endpoint. `self.client` is the raw Anthropic client
        # for the streaming path (None under a local provider, which uses the
        # provider's non-streaming complete()).
        self.provider = get_text_provider()
        if self.provider is None:
            raise RuntimeError(
                "No usable LLM provider configured. Set ANTHROPIC_API_KEY "
                "(LLM_PROVIDER=anthropic) or LLM_BASE_URL + LLM_MODEL "
                "(LLM_PROVIDER=openai). See .env.example."
            )
        self.client = self.provider.raw_client
        self.model = self.provider.default_model
        # Decompose runs on a faster model; synthesis stays on `model`.
        self.decompose_model = self.provider.decompose_model
        self.tool_registry = ToolRegistry()

    async def analyze(
        self,
        query: str,
        progress_callback=None,
        event_callback=None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> ImpactAssessment:
        """Run the full analysis pipeline for an analyst's question.

        ``progress_callback(msg: str)`` gets human-readable log lines (legacy).
        ``event_callback(event: dict)`` gets STRUCTURED events so a UI can render
        the agent swarm live. Event shapes:
          {"type":"phase","name":"recall|decompose|execute|synthesize|complete","status":...}
          {"type":"memory","items":[{"text","confidence","sources"}]}
          {"type":"plan","steps":[{"step","description","tools":[name,...]}]}
          {"type":"tool","step","name","domain","task","status":"running"} then
          {"type":"tool","step","name","domain","status":"done|error","summary","ms"}
        Both callbacks are optional and independent.

        ``user_id`` scopes long-term memory recall (Phase 5). When set, this
        analyst's prior findings are recalled BEFORE decomposition and injected
        into the plan + synthesis prompts as UNVERIFIED prior work — never as a
        cited source. Without it, the pipeline is exactly as before.
        """

        def _emit(msg: str) -> None:
            print(msg)
            if progress_callback:
                progress_callback(msg)

        def _event(ev: dict[str, Any]) -> None:
            if event_callback:
                event_callback(ev)

        _emit(f"Query received: {query[:120]}")

        # Step 0: Recall what this analyst already knows (Phase 5). Cheap — one
        # embed + one FT.SEARCH (~20-40ms), or a lexical scan when the index is
        # off. Runs BEFORE decompose so it can change the PLAN, not just the prose.
        recalled = await self._recall(query, user_id, session_id, _emit)
        if recalled:
            _event(
                {
                    "type": "memory",
                    "items": [
                        {
                            "text": m["text"],
                            "confidence": m["confidence"],
                            "sources": [s.get("name") for s in m.get("sources") or []],
                        }
                        for m, _ in recalled
                    ],
                }
            )

        # Step 1: Decompose the question into a research plan
        _emit("[1/4] Decomposing question into research plan...")
        _event({"type": "phase", "name": "decompose", "status": "start"})
        plan = await self._decompose(query, memory=recalled)
        _emit(f"[1/4] Research plan: {len(plan)} step(s) identified")
        _event(
            {
                "type": "plan",
                "steps": [
                    {
                        "step": s.get("step", i),
                        "description": s.get("description", ""),
                        "tools": _step_tool_names(s),
                    }
                    for i, s in enumerate(plan)
                ],
            }
        )

        # Step 2: Execute the research plan (the parallel agent swarm)
        _emit("[2/4] Executing research plan...")
        _event({"type": "phase", "name": "execute", "status": "start"})
        tool_results = await self._execute_plan(plan, _emit, _event)
        _emit(f"[2/4] Collected results from {len(tool_results)} research step(s)")

        # Step 3: Synthesize results
        _emit("[3/4] Synthesizing findings with Claude...")
        _event({"type": "phase", "name": "synthesize", "status": "start"})
        assessment = await self._synthesize(query, tool_results, on_event=_event, memory=recalled)
        assessment.tool_results = tool_results

        # Step 4: Done
        _emit("[4/4] Analysis complete.")
        _event({"type": "phase", "name": "complete", "status": "done"})

        return assessment

    async def _recall(self, query: str, user_id: str | None, session_id: str | None, emit) -> list:
        """Recall this analyst's prior facts relevant to the query. [] if none/off.

        Two prongs, merged:
          1. **Semantic** — vector KNN of the query over the user's memories.
          2. **Entity-anchored** — if the session's working memory knows which
             entities this thread is about, recall memories tagged with those
             entities. This is what resolves anaphora: "what else is exposed to
             *that supply chain*?" shares almost no words with "DRAM line / UMC",
             but the thread's entities (Fujian Jinhua, UMC, Micron) pin it to the
             semiconductor context. Bare-query recall alone would drift toward
             generic "supply chain / logistics" matches.

        Best-effort: recall must never break an analysis.
        """
        if not user_id:
            return []
        try:
            from src.common import agent_memory

            merged: dict[str, tuple[dict, float]] = {}

            for m, score in await agent_memory.recall(query, user_id=user_id, k=8):
                merged[m["memory_id"]] = (m, score)

            # Entity-anchored pass, using what the thread is already about.
            entity_names = self._session_entities(session_id, user_id)
            if entity_names:
                anchored = await agent_memory.recall(
                    query, user_id=user_id, k=8, entity_names=entity_names
                )
                for m, score in anchored:
                    # Anchored hits are more on-topic; keep the higher score.
                    prev = merged.get(m["memory_id"])
                    if prev is None or score > prev[1]:
                        merged[m["memory_id"]] = (m, score)

            hits = sorted(merged.values(), key=lambda x: x[1], reverse=True)[:8]
            if hits:
                emit(f"[0/4] Recalled {len(hits)} prior finding(s) for this analyst.")
            return hits
        except Exception as exc:  # noqa: BLE001
            print(f"  [recall] skipped: {exc}")
            return []

    def _session_entities(self, session_id: str | None, user_id: str | None) -> list[str]:
        """Entity names this thread is about, from working memory ([] if none)."""
        if not session_id or not user_id:
            return []
        try:
            from src.common import agent_memory

            wm = agent_memory.get_working(session_id, user_id)
            if wm is None:
                return []
            return [e.get("name") for e in wm.entities if e.get("name")][:12]
        except Exception:  # noqa: BLE001
            return []

    async def _decompose(self, query: str, memory: list | None = None) -> list[dict[str, Any]]:
        """Use the LLM to decompose the question into a research plan.

        When ``memory`` (recalled prior facts) is present, a block is appended to
        the decomposition prompt so the plan can be SHARPER — skip reconfirming
        HIGH-confidence facts, push past what's known, verify shaky ones.
        """
        user_content = DECOMPOSITION_PROMPT.format(query=query)
        mem_block = _format_memory_block(memory)
        if mem_block:
            user_content = f"{user_content}\n\n{DECOMPOSITION_MEMORY_BLOCK}\n{mem_block}"

        text = await self.provider.complete(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=3000,
            model=self.decompose_model,
        )
        # Extract JSON from response (may be wrapped in markdown code blocks)
        json_str = _extract_json(text)
        try:
            plan = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback: create a basic plan
            plan = self._fallback_plan(query)
        # The LLM sometimes wraps the steps in an object ({"steps":[...]} /
        # {"plan":[...]}) instead of returning a bare list. Coerce to a list of
        # step dicts so the plan event + _execute_plan never iterate dict keys
        # (which raised "'str' object has no attribute 'get'" and failed the run).
        plan = _coerce_plan_list(plan)
        if not plan:
            plan = self._fallback_plan(query)
        # Hard backstop on fan-out: trim tools-per-step / total so a verbose plan
        # can't blow up execute time or truncate synthesis, regardless of what the
        # model returned. Prompt also nudges toward this, but never trust the model.
        plan = _cap_plan(plan, config.orchestrator_max_tools)
        return plan

    async def _execute_plan(
        self, plan: list[dict[str, Any]], emit=None, on_event=None
    ) -> dict[str, Any]:
        """Execute the research plan, running independent steps in parallel."""
        results: dict[str, Any] = {}
        completed_steps: set[int] = set()
        # Preload so tool_domain() resolves on the first "running" event.
        await self.tool_registry._ensure_loaded()

        # One semaphore for the whole run caps TOTAL concurrent tool calls across
        # every parallel step (tools within a step now run concurrently too). This
        # is the biggest speed win and also bounds peak memory — each in-flight
        # agent holds large GDELT/Comtrade/sanctions JSON.
        sem = asyncio.Semaphore(max(1, config.orchestrator_max_concurrency))

        def _log(msg: str) -> None:
            print(msg)
            if emit:
                emit(msg)

        # Group steps by dependency level for parallel execution
        while len(completed_steps) < len(plan):
            # Find steps whose dependencies are all met
            ready = []
            for step in plan:
                step_num = step.get("step", 0)
                if step_num in completed_steps:
                    continue
                deps = step.get("depends_on", [])
                if all(d in completed_steps for d in deps):
                    ready.append(step)

            if not ready:
                # Avoid infinite loop if dependencies can't be resolved
                break

            for step in ready:
                desc = step.get("description", f"step {step.get('step', '?')}")
                _log(f"  Running: {desc}")

            # Execute ready steps in parallel
            tasks = [
                self._execute_step(
                    step, results, on_event=on_event, step_num=step.get("step", 0), sem=sem
                )
                for step in ready
            ]
            step_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, result in zip(ready, step_results):
                step_num = step.get("step", 0)
                completed_steps.add(step_num)
                if isinstance(result, Exception):
                    results[f"step_{step_num}"] = {
                        "error": str(result),
                        "description": step.get("description", ""),
                    }
                    _log(f"  Step {step_num} failed: {result}")
                else:
                    results[f"step_{step_num}"] = result
                    _log(f"  Step {step_num} done: {step.get('description', '')}")

        return results

    async def _execute_step(
        self,
        step: dict[str, Any],
        prior_results: dict[str, Any],
        on_event=None,
        step_num: int = 0,
        sem: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """Execute a single research step by calling the specified tools.

        Tools within a step are independent (dependencies are modeled at the STEP
        level), so they run CONCURRENTLY, gated by the shared run-wide semaphore.
        Emits a structured {type:"tool", status:"running"|"done"|"error"} event per
        tool call (when on_event is set) so a UI can render each data-source agent
        lighting up live with a result chip + duration. Event ordering across the
        step's tools is non-deterministic; the UI keys agents by (step, name).
        """
        step_results: dict[str, Any] = {}
        tools = step.get("tools", [])
        task = step.get("description", "")
        prior_ids = _collect_identifiers(prior_results)

        async def _run_one(tool_call: Any) -> None:
            if isinstance(tool_call, str):
                # Handle Python-style call strings: "get_stock_profile('SMCI')"
                tool_name, params = _parse_string_tool_call(tool_call)
            else:
                # LLM may use "tool"/"params" or "name"/"parameters" interchangeably
                tool_name = tool_call.get("tool") or tool_call.get("name") or ""
                params = tool_call.get("params") or tool_call.get("parameters") or {}
            # Fill "{{...}}" references with identifiers produced by earlier steps.
            params = _resolve_params(params, prior_ids)

            domain = self.tool_registry.tool_domain(tool_name) if tool_name else "unknown"
            if on_event and tool_name:
                on_event(
                    {
                        "type": "tool",
                        "step": step_num,
                        "name": tool_name,
                        "domain": domain,
                        "task": task,
                        "status": "running",
                    }
                )
            started = time.perf_counter()
            try:
                if _has_unresolved(params):
                    # A required upstream value never materialized — skip the doomed
                    # call instead of sending a literal "{{...}}" that 404s.
                    result = {"error": "Skipped — required input not produced by earlier steps"}
                else:
                    if sem is not None:
                        async with sem:
                            result = await self.tool_registry.call_tool(tool_name, params)
                    else:
                        result = await self.tool_registry.call_tool(tool_name, params)
                step_results[tool_name] = result
                status = "error" if isinstance(result, dict) and result.get("error") else "done"
            except Exception as e:
                result = {"error": str(e)}
                step_results[tool_name] = result
                status = "error"
            if on_event and tool_name:
                on_event(
                    {
                        "type": "tool",
                        "step": step_num,
                        "name": tool_name,
                        "domain": domain,
                        "status": status,
                        "summary": _summarize_tool_result(result),
                        # Real findings for the live "intelligence feed" — top rows
                        # + sources, so the UI can stream what each agent found as
                        # it lands (not just a count chip).
                        "detail": _extract_findings(result),
                        "ms": int((time.perf_counter() - started) * 1000),
                    }
                )

        # Run the step's tools concurrently; the semaphore bounds total in-flight
        # calls across the whole run. return_exceptions keeps one bad tool from
        # cancelling its siblings (per-tool errors are already caught above).
        await asyncio.gather(*(_run_one(tc) for tc in tools), return_exceptions=True)

        return {
            "description": step.get("description", ""),
            "results": step_results,
        }

    async def _stream_synthesis(self, system: str, user_content: str, on_event=None) -> str:
        """Run the synthesis call, streaming the executive_summary out live via on_event.

        Emits {"type":"synthesis","text": <partial exec summary>} as Claude writes,
        so the "Synthesizing…" wait fills with the answer appearing in real time.
        Falls back to a plain call if streaming is unavailable.
        """
        msgs = [{"role": "user", "content": user_content}]
        # Non-streaming path: no live callback, or a non-Anthropic provider (which
        # has no Anthropic streaming API). The default Anthropic streaming path
        # below is unchanged.
        if on_event is None or not self.provider.is_anthropic:
            return await self.provider.complete(
                system=system, messages=msgs, max_tokens=16000, model=self.model
            )
        try:
            buffer = ""
            last_len = 0
            async with self.client.messages.stream(
                model=self.model, max_tokens=16000, system=system, messages=msgs
            ) as stream:
                async for chunk in stream.text_stream:
                    buffer += chunk
                    partial = _partial_field(buffer, "executive_summary")
                    if partial is not None and len(partial) - last_len >= 40:
                        last_len = len(partial)
                        on_event({"type": "synthesis", "text": partial})
                final = await stream.get_final_message()
            full = final.content[0].text
            done = _partial_field(full, "executive_summary")
            if done:
                on_event({"type": "synthesis", "text": done})
            return full
        except Exception as exc:
            print(f"  [synthesize] streaming unavailable ({exc}); using non-streaming call")
            resp = await self.client.messages.create(
                model=self.model, max_tokens=16000, system=system, messages=msgs
            )
            return resp.content[0].text

    async def _synthesize(
        self, query: str, tool_results: dict[str, Any], on_event=None, memory: list | None = None
    ) -> ImpactAssessment:
        """Use Claude to synthesize tool results into a final assessment."""
        # Determine scenario type from results
        scenario_type = "sanction_impact"  # default

        # Count steps that returned only errors vs steps with real data
        error_only_steps = [
            k
            for k, v in tool_results.items()
            if isinstance(v, dict) and set(v.keys()) <= {"error", "description"}
        ]
        data_steps = [k for k in tool_results if k not in error_only_steps]
        if error_only_steps:
            print(
                f"  [synthesize] {len(error_only_steps)} step(s) returned errors only: {error_only_steps}"
            )

        # Compact tool results — strip large arrays/raw data to stay within context
        compacted = _compact_tool_results(tool_results)
        results_text = json.dumps(compacted, indent=2, default=str)

        # Truncate if still too long for context window
        truncation_note = ""
        _LIMIT = 80000
        if len(results_text) > _LIMIT:
            # Preserve metadata about the truncation so Claude knows data was cut
            chars_dropped = len(results_text) - _LIMIT
            truncation_note = (
                f"\n\n[DATA TRUNCATED: {chars_dropped:,} characters dropped. "
                f"{len(data_steps)} steps had data; {len(error_only_steps)} steps errored. "
                "Findings that rely on data from truncated steps should be rated LOW confidence.]"
            )
            results_text = results_text[:_LIMIT] + truncation_note
            print(f"  [synthesize] Tool results truncated: {chars_dropped:,} chars dropped")

        synthesis_system = SYSTEM_PROMPT + SYNTHESIS_SYSTEM_SUPPLEMENT
        user_content = SYNTHESIS_PROMPT.format(
            query=query,
            scenario_type=scenario_type,
            tool_results=results_text,
        )
        mem_block = _format_memory_block(memory)
        if mem_block:
            # Context only. Every claim still stands on the tool results above, and
            # _merge_sources never harvests memory — provenance stays live-tool-only.
            user_content = f"{user_content}\n\n{SYNTHESIS_MEMORY_BLOCK}\n{mem_block}"
        text = await self._stream_synthesis(synthesis_system, user_content, on_event)
        json_str = _extract_json(text)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[synthesize] JSON parse failed ({e}); retrying with strict JSON instruction...")
            print(f"  [synthesize] Failed response (first 500 chars): {text[:500]!r}")

            # Retry: send the broken response back and ask for clean JSON only
            retry_text = await self.provider.complete(
                system=SYSTEM_PROMPT + SYNTHESIS_SYSTEM_SUPPLEMENT,
                max_tokens=16000,
                messages=[
                    {
                        "role": "user",
                        "content": SYNTHESIS_PROMPT.format(
                            query=query,
                            scenario_type=scenario_type,
                            tool_results=results_text,
                        ),
                    },
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your response was not valid JSON. Output ONLY the raw JSON object "
                            "— no markdown, no prose, no explanation. Start your response with { "
                            "and end with }."
                        ),
                    },
                ],
                model=self.model,
            )
            retry_json_str = _extract_json(retry_text)
            try:
                data = json.loads(retry_json_str)
                print("[synthesize] Retry succeeded.")
            except json.JSONDecodeError as e2:
                print(f"[synthesize] Retry also failed ({e2}). Extracting findings from raw text.")
                # Last resort: surface the raw narrative as a single finding
                # so the user sees something rather than a blank panel
                summary = text[:600].strip()
                data = {
                    "scenario_type": scenario_type,
                    "executive_summary": summary,
                    "findings": [
                        {
                            "category": "Analysis",
                            "finding": text[:2000].strip(),
                            "confidence": "MEDIUM",
                        }
                    ],
                    "friendly_fire": [],
                    "recommendations": [],
                    "confidence_summary": {},
                    "sources_used": [],
                }

        # Build ImpactAssessment
        try:
            st = ScenarioType(data.get("scenario_type", scenario_type))
        except ValueError:
            st = ScenarioType.SANCTION_IMPACT

        confidence_map = {}
        for k, v in data.get("confidence_summary", {}).items():
            try:
                confidence_map[k] = Confidence(v)
            except ValueError:
                confidence_map[k] = Confidence.LOW

        # Sources: augment LLM-derived sources_used with real ToolResponse.sources
        # envelopes harvested from tool_results, dedup'd by (name, record_url or url).
        # The LLM-derived list stays as a fallback safety net for pipelines that
        # bypass the ToolResponse envelope; tool-derived entries take precedence
        # because they carry real URLs/descriptions while LLM rollups don't.
        merged_sources = _merge_sources(
            tool_results,
            llm_source_names=[str(s) for s in data.get("sources_used", []) if s],
        )

        # Build the entity graph from the raw tool results so the "Ask Anything"
        # (orchestrator) path gets a populated graph — previously this was left as
        # the default empty EntityGraph, so the deep-analysis view never showed a
        # graph. The frontend now renders it in the unified EntityGraphSection (#34).
        entity_graph = build_graph_from_results(tool_results)

        return ImpactAssessment(
            query=AnalystQuery(raw_query=query, scenario_type=st),
            scenario_type=st,
            executive_summary=data.get("executive_summary", ""),
            findings=data.get("findings", []),
            friendly_fire=data.get("friendly_fire", []),
            confidence_summary=confidence_map,
            sources=merged_sources,
            recommendations=data.get("recommendations", []),
            entity_graph=entity_graph,
        )

    def _fallback_plan(self, query: str) -> list[dict[str, Any]]:
        """Generate a basic research plan when Claude's decomposition fails."""
        return [
            {
                "step": 1,
                "description": "Search for target entities in sanctions databases",
                "tools": [{"tool": "search_sanctions", "params": {"query": query}}],
                "depends_on": [],
            },
            {
                "step": 2,
                "description": "Resolve entity and map corporate structure",
                "tools": [{"tool": "search_entity", "params": {"query": query}}],
                "depends_on": [],
            },
            {
                "step": 3,
                "description": "Search for market data on target entity",
                "tools": [{"tool": "search_market_entity", "params": {"query": query}}],
                "depends_on": [],
            },
            {
                "step": 4,
                "description": "Check geopolitical context",
                "tools": [{"tool": "search_events", "params": {"query": query}}],
                "depends_on": [],
            },
        ]


def _walk_tool_sources(node: Any) -> list[dict[str, Any]]:
    """Recursively walk a tool_results structure and collect any `sources` lists.

    Tool agents return ToolResponse envelopes whose sources are surfaced as either
    (a) SourceReference Pydantic instances or (b) plain dicts after JSON round-trip.
    This walker handles both, plus nested step results.
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        raw = node.get("sources")
        if isinstance(raw, list):
            for item in raw:
                if hasattr(item, "model_dump"):  # SourceReference instance
                    found.append(item.model_dump(mode="json"))
                elif isinstance(item, dict):
                    found.append(item)
                elif isinstance(item, str) and item.strip():
                    found.append({"name": item.strip()})
        for v in node.values():
            if v is not raw:
                found.extend(_walk_tool_sources(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_walk_tool_sources(v))
    return found


def _looks_like_opaque_id(name: str) -> bool:
    """True for machine IDs (hashes, Sayari entity_ids) that aren't human source names.

    Real source names are short (FRED, GLEIF) or contain spaces (OFAC SDN,
    Trade.gov CSL); opaque keys are long, space-less, hex- or digit-heavy tokens
    that should never be shown to an analyst as a "source".
    """
    import re

    n = name.strip()
    if " " in n or len(n) < 16:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{16,}", n):  # hex record hash / md5-ish
        return True
    # long single token with several digits and no spaces → opaque key
    return sum(c.isdigit() for c in n) >= 4 and re.fullmatch(r"[A-Za-z0-9_\-]+", n) is not None


def _format_memory_block(memory: list | None) -> str:
    """Render recalled memories as prompt lines, or "" when there are none.

    Each line: ``- [CONFIDENCE, entities] text  (src: ...)``. Kept compact — this
    rides on every decompose + synthesis prompt when memory is present.
    """
    if not memory:
        return ""
    lines: list[str] = []
    for item in memory[:8]:
        m = item[0] if isinstance(item, tuple) else item
        if not isinstance(m, dict) or not m.get("text"):
            continue
        conf = m.get("confidence", "?")
        ents = ", ".join(m.get("entity_names") or [])
        srcs = ", ".join(s.get("name", "") for s in (m.get("sources") or []) if s.get("name"))
        tag = f"{conf}" + (f", {ents}" if ents else "")
        line = f"- [{tag}] {m['text']}"
        if srcs:
            line += f"  (src: {srcs})"
        lines.append(line)
    return "\n".join(lines)


def _merge_sources(
    tool_results: dict[str, Any], llm_source_names: list[str]
) -> list[SourceReference]:
    """Combine ToolResponse-derived sources with the LLM's sources_used list.

    Real tool sources take precedence (they carry URLs and descriptions). LLM
    rollup names only contribute when no tool source already covers them.
    Dedup key is (name lowered, record_url or url).
    """
    merged: dict[tuple[str, str], SourceReference] = {}

    for raw in _walk_tool_sources(tool_results):
        name = str(raw.get("name") or "").strip()
        if not name or _looks_like_opaque_id(name):
            continue
        url = raw.get("url") or None
        record_url = raw.get("record_url") or None
        key = (name.lower(), record_url or url or "")
        existing = merged.get(key)
        if existing is None:
            merged[key] = SourceReference(
                name=name,
                url=url,
                record_url=record_url,
                description=raw.get("description") or None,
                dataset_version=raw.get("dataset_version") or None,
            )
        else:
            # Fill in missing fields from a richer duplicate
            if not existing.url and url:
                existing.url = url
            if not existing.record_url and record_url:
                existing.record_url = record_url
            if not existing.description and raw.get("description"):
                existing.description = raw["description"]

    for name in llm_source_names:
        clean = name.strip()
        if not clean:
            continue
        # Suppress LLM rollup labels that look like "Vessel Intel: X assessment"
        # — they collide with the per-API entries and add no provenance value.
        if ":" in clean and any(w in clean.lower() for w in ("assessment", "analysis", "intel")):
            continue
        if _looks_like_opaque_id(clean):  # drop LLM-echoed entity_ids / hashes
            continue
        key = (clean.lower(), "")
        # Don't override a tool-derived entry with the same name
        if any(k[0] == clean.lower() for k in merged):
            continue
        merged[key] = SourceReference(name=clean)

    return list(merged.values())


def _compact_tool_results(
    results: dict[str, Any], max_list: int = 10, max_str: int = 500
) -> dict[str, Any]:
    """Recursively compact tool results to fit within synthesis context window.

    Truncates long strings, caps list lengths, and strips raw binary/HTML data.
    Reduces 6MB+ tool dumps to ~50-100KB of meaningful summaries.
    """
    if isinstance(results, dict):
        out = {}
        for k, v in results.items():
            # Skip keys that are typically huge and low-signal
            if k in (
                "raw_html",
                "raw_response",
                "raw_data",
                "price_history",
                "historical_data",
                "chart_data",
                "curve",
                "positions",
            ):
                if isinstance(v, list):
                    out[k] = f"[{len(v)} items omitted]"
                else:
                    out[k] = "[omitted]"
            else:
                out[k] = _compact_tool_results(v, max_list, max_str)
        return out
    elif isinstance(results, list):
        if len(results) > max_list:
            compacted = [
                _compact_tool_results(item, max_list, max_str) for item in results[:max_list]
            ]
            compacted.append(f"... and {len(results) - max_list} more items")
            return compacted
        return [_compact_tool_results(item, max_list, max_str) for item in results]
    elif isinstance(results, str):
        if len(results) > max_str:
            return results[:max_str] + f"... [{len(results) - max_str} chars truncated]"
        return results
    else:
        return results


def _coerce_plan_list(plan: Any) -> list[dict[str, Any]]:
    """Normalize a decomposed plan into a list of step dicts.

    The decomposition LLM is asked for a bare JSON list of steps, but it
    intermittently wraps them in an object ({"steps":[...]}, {"plan":[...]}) or
    returns a single step dict. Anything that isn't a list of dicts previously
    caused the run to crash when iterated. Always return a clean list of dicts
    (possibly empty, so the caller can fall back to a basic plan).
    """
    if isinstance(plan, list):
        return [s for s in plan if isinstance(s, dict)]
    if isinstance(plan, dict):
        for key in ("steps", "plan", "research_plan", "tasks", "actions"):
            value = plan.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
        # A lone step object (has step-shaped keys) → wrap it.
        if any(k in plan for k in ("tools", "description", "step")):
            return [plan]
        # Last resort: first list-of-dicts value anywhere in the object.
        for value in plan.values():
            if isinstance(value, list) and any(isinstance(s, dict) for s in value):
                return [s for s in value if isinstance(s, dict)]
    return []


def _cap_plan(
    plan: list[dict[str, Any]], max_total: int, max_per_step: int = 4
) -> list[dict[str, Any]]:
    """Trim a plan's tool fan-out to a hard budget, preserving step structure.

    Caps tools-per-step at ``max_per_step`` and total tools across the plan at
    ``max_total`` (a verbose decomposition otherwise yields 60+ agents → long
    execute + truncated synthesis). Steps and their ``depends_on`` are kept so a
    later step that consumed an earlier one's output still resolves; only the
    surplus tool calls are dropped. A step trimmed to zero tools is preserved as
    an empty step (harmless — it just produces no results).
    """
    if max_total <= 0:
        return plan
    budget = max_total
    for step in plan:
        tools = step.get("tools") or []
        if not isinstance(tools, list):
            continue
        keep = tools[: max(0, max_per_step)]
        if len(keep) > budget:
            keep = keep[:budget]
        budget -= len(keep)
        step["tools"] = keep
    return plan


def _step_tool_names(step: dict[str, Any]) -> list[str]:
    """Tool names a plan step will call (for the 'plan' event)."""
    names: list[str] = []
    for tc in step.get("tools", []) or []:
        if isinstance(tc, str):
            name, _ = _parse_string_tool_call(tc)
        else:
            name = tc.get("tool") or tc.get("name") or ""
        if name:
            names.append(name)
    return names


def _summarize_tool_result(result: Any) -> str:
    """Short chip for a tool result, e.g. '3 results, high' or 'error'."""
    if not isinstance(result, dict):
        return "ok"
    if result.get("error"):
        return "error"
    data = result.get("data", result)
    n = len(data) if isinstance(data, (list, dict)) else (1 if data else 0)
    parts: list[str] = []
    if n:
        parts.append(f"{n} result{'s' if n != 1 else ''}")
    conf = result.get("confidence")
    if conf:
        parts.append(str(conf).lower())
    return ", ".join(parts) or "ok"


# Keys under a tool's `data` that commonly hold the list of findings to surface.
_FINDING_LIST_KEYS = (
    "articles",
    "results",
    "hits",
    "entities",
    "owners",
    "designations",
    "matches",
    "items",
    "companies",
    "records",
    "events",
    "holders",
    "partners",
    "key_players",
    "nodes",
)


def _label_item(entry: Any) -> str:
    """One short human label for a single finding (row) in a tool result."""
    if isinstance(entry, str):
        return entry.strip()[:140]
    if isinstance(entry, dict):
        for primary in ("title", "name", "label", "entity", "company", "headline", "description"):
            val = entry.get(primary)
            if isinstance(val, str) and val.strip():
                for qual in (
                    "source",
                    "program",
                    "list",
                    "country",
                    "date",
                    "ticker",
                    "lei",
                    "status",
                ):
                    qv = entry.get(qual)
                    if isinstance(qv, str) and qv.strip():
                        return f"{val.strip()[:110]} — {qv.strip()[:40]}"
                return val.strip()[:140]
        for val in entry.values():  # fallback: first scalar
            if isinstance(val, (str, int, float)) and str(val).strip():
                return str(val)[:140]
    return ""


def _partial_field(buffer: str, field: str) -> str | None:
    """Best-effort decode of a (possibly incomplete) JSON string field from a stream buffer.

    Used to surface the executive_summary as it streams, before the full JSON closes.
    Stops cleanly at an incomplete trailing escape so partial output never shows junk.
    """
    import re

    m = re.search(r'"' + re.escape(field) + r'"\s*:\s*"', buffer)
    if not m:
        return None
    out: list[str] = []
    i, n = m.end(), len(buffer)
    while i < n:
        c = buffer[i]
        if c == "\\":
            if i + 1 >= n:
                break  # incomplete escape at the buffer edge — stop here
            nxt = buffer[i + 1]
            out.append(
                {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt)
            )
            i += 2
            continue
        if c == '"':
            break  # closing quote — field complete
        out.append(c)
        i += 1
    return "".join(out)


def _friendly_error(raw: str) -> str:
    """Map a raw tool/HTTP error to a short, user-safe message.

    Never leak URLs, stack text, MDN links, or unresolved template placeholders
    (e.g. "{{entity_id}}") to end users — collapse to a calm, human reason.
    """
    low = (raw or "").lower()
    if "404" in low or "not found" in low:
        return "No matching records"
    if "429" in low or "too many requests" in low or "rate limit" in low:
        return "Source busy — skipped"
    if "timeout" in low or "timed out" in low:
        return "Source timed out"
    if "401" in low or "403" in low or "unauthorized" in low or "forbidden" in low:
        return "Source unavailable"
    if any(code in low for code in ("500", "502", "503", "504")) or "server error" in low:
        return "Source temporarily unavailable"
    return "No data returned"


_PLACEHOLDER_RE = __import__("re").compile(r"\{\{.*?\}\}")
# Identifier fields worth carrying from one step's output into a later step's params.
_ID_KEYS = ("entity_id", "sayari_id", "id", "lei", "ticker", "symbol", "imo", "mmsi")


def _collect_identifiers(prior_results: Any) -> dict[str, str]:
    """Gather usable identifiers (entity_id, lei, ticker, ...) from prior step outputs.

    Lets a later step that referenced a value it couldn't know upfront (e.g. a
    Sayari entity_id from a resolve step) actually receive it, instead of sending
    a literal "{{...}}" placeholder that 404s.
    """
    ids: dict[str, str] = {}

    def scan(obj: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                kl = key.lower()
                if kl in _ID_KEYS and isinstance(val, (str, int)) and str(val).strip():
                    ids.setdefault(kl, str(val))
                else:
                    scan(val, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:10]:
                scan(item, depth + 1)

    scan(prior_results)
    return ids


def _resolve_placeholder(param_name: str, raw: str, ids: dict[str, str]) -> str | None:
    """Pick the best prior identifier for a "{{...}}" param value (heuristic by hint)."""
    text = f"{param_name} {raw}".lower()
    if "sayari" in text:
        order = ("sayari_id", "entity_id", "id")
    elif "lei" in text:
        order = ("lei",)
    elif "ticker" in text or "symbol" in text:
        order = ("ticker", "symbol")
    elif "imo" in text:
        order = ("imo",)
    elif "mmsi" in text:
        order = ("mmsi",)
    else:
        order = ("entity_id", "sayari_id", "id")
    for key in order:
        if ids.get(key):
            return ids[key]
    return None


def _resolve_params(params: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    """Substitute "{{...}}" placeholder values in tool params from prior identifiers."""
    if not isinstance(params, dict):
        return params
    out: dict[str, Any] = {}
    for key, val in params.items():
        if isinstance(val, str) and _PLACEHOLDER_RE.search(val):
            sub = _resolve_placeholder(key, val, ids)
            out[key] = sub if sub is not None else val
        else:
            out[key] = val
    return out


def _has_unresolved(params: dict[str, Any]) -> bool:
    """True if any param still holds a literal "{{...}}" reference after resolution."""
    if not isinstance(params, dict):
        return False
    return any(isinstance(v, str) and _PLACEHOLDER_RE.search(v) for v in params.values())


def _extract_findings(result: Any) -> dict[str, Any]:
    """Compact, demo-friendly findings for the live feed: a few top rows + sources.

    Uniform shape across heterogeneous tools so a UI can stream each agent's
    actual output (sanctions hits, headlines, owners, ...) the moment it lands.
    Returns {"items": [str, ...], "confidence"?, "sources"?, "error"?}.
    """
    if not isinstance(result, dict):
        return {"items": []}
    if result.get("error"):
        return {"items": [], "error": _friendly_error(str(result.get("error")))}

    data = result.get("data", result)
    items: list[str] = []
    if isinstance(data, dict):
        for key in _FINDING_LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list) and value:
                items = [_label_item(e) for e in value[:5]]
                break
        if not items:
            # Generic fallback: the first list of dicts/strings anywhere in data,
            # so tool-specific key names (sanctions matches, etc.) still surface.
            for value in data.values():
                if isinstance(value, list) and value and isinstance(value[0], (dict, str)):
                    candidate = [_label_item(e) for e in value[:5]]
                    if any(candidate):
                        items = candidate
                        break
        if not items:  # scalar dict — surface a few key:value pairs
            for key, value in data.items():
                if (
                    isinstance(value, (str, int, float))
                    and str(value).strip()
                    and not isinstance(value, bool)
                ):
                    items.append(f"{key}: {str(value)[:80]}")
                if len(items) >= 5:
                    break
    elif isinstance(data, list):
        items = [_label_item(e) for e in data[:5]]

    out: dict[str, Any] = {"items": [i for i in items if i][:5]}
    conf = result.get("confidence")
    if conf:
        out["confidence"] = str(conf).lower()
    sources = [
        s.get("name")
        for s in (result.get("sources") or [])
        if isinstance(s, dict) and s.get("name") and not _looks_like_opaque_id(s["name"])
    ]
    if sources:
        out["sources"] = sources[:3]
    return out


def _parse_string_tool_call(call_str: str) -> tuple[str, dict[str, Any]]:
    """Parse a Python-style tool call string like 'get_stock_profile(\"SMCI\")' into (name, params).

    Returns (tool_name, {}) if parsing fails, leaving the error to be caught by call_tool.
    """
    import re

    call_str = call_str.strip()
    m = re.match(r"^(\w+)\s*\((.*)\)\s*$", call_str, re.DOTALL)
    if not m:
        return call_str, {}

    tool_name = m.group(1)
    args_str = m.group(2).strip()
    if not args_str:
        return tool_name, {}

    params: dict[str, Any] = {}

    # Try to find keyword args first: key='value' or key="value" or key=123
    kw_matches = re.findall(r'(\w+)\s*=\s*(?:"([^"]*?)"|\'([^\']*?)\'|(\d+))', args_str)
    if kw_matches:
        for kw_match in kw_matches:
            key = kw_match[0]
            # Take first non-empty capture group as the value
            val: Any = next((v for v in kw_match[1:] if v != ""), "")
            params[key] = int(val) if val.isdigit() else val
    else:
        # Positional args only — try to extract string values
        pos_vals = re.findall(r'"([^"]*?)"|\'([^\']*?)\'|(\d+)', args_str)
        positional = [next(v for v in grp if v != "") for grp in pos_vals]
        # Map positional args to common parameter names based on typical tool signatures
        if len(positional) >= 1:
            params["query"] = positional[0]

    return tool_name, params


def _extract_balanced_json(text: str, start: int, open_ch: str, close_ch: str) -> str | None:
    """Extract a balanced JSON object/array from text[start:].

    Properly tracks string literals so that { } [ ] inside quoted values
    do not corrupt the bracket depth counter — the previous implementation
    would terminate early when a finding contained curly braces in its text,
    returning incomplete JSON that failed json.loads.
    """
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _extract_json(text: str) -> str:
    """Extract the outermost JSON object from a response.

    Strategy order:
    1. Markdown ```json ... ``` code block (validate with json.loads)
    2. Any ``` ... ``` code block (validate with json.loads)
    3. Scan for first { that starts a parse-valid JSON object
    4. Return the raw text as a last resort
    """
    # 1 & 2: code blocks
    for prefix in ("```json", "```"):
        if prefix in text:
            block_start = text.index(prefix) + len(prefix)
            block_end = text.find("```", block_start)
            if block_end != -1:
                candidate = text[block_start:block_end].strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass  # fall through to raw scan

    # 3: scan for the first valid { ... } pair using string-aware depth tracking
    pos = 0
    while pos < len(text):
        idx = text.find("{", pos)
        if idx == -1:
            break
        candidate = _extract_balanced_json(text, idx, "{", "}")
        if candidate:
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        pos = idx + 1

    # 4: return raw text; caller will handle parse failure
    return text


async def main() -> None:
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if not query:
        print('Usage: uv run python -m src.orchestrator.main "Your question here"')
        print("\nExample:")
        print(
            '  uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"'
        )
        sys.exit(1)

    try:
        orchestrator = Orchestrator()
    except RuntimeError as e:
        print(str(e))
        sys.exit(1)
    assessment = await orchestrator.analyze(query)

    # Print results
    print(f"\nScenario Type: {assessment.scenario_type.value}")
    print(f"\nExecutive Summary:\n{assessment.executive_summary}")
    print(f"\nFindings ({len(assessment.findings)}):")
    for i, f in enumerate(assessment.findings, 1):
        print(
            f"  {i}. [{f.get('confidence', 'N/A')}] {f.get('category', 'General')}: {f.get('finding', '')}"
        )
    if assessment.friendly_fire:
        print(f"\nFriendly Fire Alerts ({len(assessment.friendly_fire)}):")
        for ff in assessment.friendly_fire:
            print(f"  ⚠ {ff.get('entity', 'Unknown')}: {ff.get('details', '')}")
    if assessment.recommendations:
        print("\nRecommendations:")
        for r in assessment.recommendations:
            print(f"  • {r}")


if __name__ == "__main__":
    asyncio.run(main())
