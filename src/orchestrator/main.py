"""Orchestrator Agent — the 'quarterback' that decomposes questions and synthesizes results.

Usage:
    uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import anthropic

from src.common.config import config
from src.common.types import (
    AnalystQuery,
    Confidence,
    ImpactAssessment,
    ScenarioType,
    SourceReference,
)
from src.orchestrator.prompts import (
    DECOMPOSITION_PROMPT,
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

        self.client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
        self.model = config.model
        self.tool_registry = ToolRegistry()

    async def analyze(self, query: str, progress_callback=None) -> ImpactAssessment:
        """Run the full analysis pipeline for an analyst's question."""
        from typing import Callable

        def _emit(msg: str) -> None:
            print(msg)
            if progress_callback:
                progress_callback(msg)

        _emit(f"Query received: {query[:120]}")

        # Step 1: Decompose the question into a research plan
        _emit("[1/4] Decomposing question into research plan...")
        plan = await self._decompose(query)
        _emit(f"[1/4] Research plan: {len(plan)} step(s) identified")

        # Step 2: Execute the research plan
        _emit("[2/4] Executing research plan...")
        tool_results = await self._execute_plan(plan, _emit)
        _emit(f"[2/4] Collected results from {len(tool_results)} research step(s)")

        # Step 3: Synthesize results
        _emit("[3/4] Synthesizing findings with Claude...")
        assessment = await self._synthesize(query, tool_results)
        assessment.tool_results = tool_results

        # Step 4: Done
        _emit("[4/4] Analysis complete.")

        return assessment

    async def _decompose(self, query: str) -> list[dict[str, Any]]:
        """Use Claude to decompose the question into a research plan."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": DECOMPOSITION_PROMPT.format(query=query),
                }
            ],
        )

        text = response.content[0].text
        # Extract JSON from response (may be wrapped in markdown code blocks)
        json_str = _extract_json(text)
        try:
            plan = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback: create a basic plan
            plan = self._fallback_plan(query)
        return plan

    async def _execute_plan(self, plan: list[dict[str, Any]], emit=None) -> dict[str, Any]:
        """Execute the research plan, running independent steps in parallel."""
        results: dict[str, Any] = {}
        completed_steps: set[int] = set()

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
            tasks = [self._execute_step(step, results) for step in ready]
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
        self, step: dict[str, Any], prior_results: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single research step by calling the specified tools."""
        step_results: dict[str, Any] = {}
        tools = step.get("tools", [])

        for tool_call in tools:
            if isinstance(tool_call, str):
                # Handle Python-style call strings: "get_stock_profile('SMCI')"
                tool_name, params = _parse_string_tool_call(tool_call)
            else:
                # LLM may use "tool"/"params" or "name"/"parameters" interchangeably
                tool_name = (
                    tool_call.get("tool")
                    or tool_call.get("name")
                    or ""
                )
                params = (
                    tool_call.get("params")
                    or tool_call.get("parameters")
                    or {}
                )

            try:
                result = await self.tool_registry.call_tool(tool_name, params)
                step_results[tool_name] = result
            except Exception as e:
                step_results[tool_name] = {"error": str(e)}

        return {
            "description": step.get("description", ""),
            "results": step_results,
        }

    async def _synthesize(self, query: str, tool_results: dict[str, Any]) -> ImpactAssessment:
        """Use Claude to synthesize tool results into a final assessment."""
        # Determine scenario type from results
        scenario_type = "sanction_impact"  # default

        # Count steps that returned only errors vs steps with real data
        error_only_steps = [
            k for k, v in tool_results.items()
            if isinstance(v, dict) and set(v.keys()) <= {"error", "description"}
        ]
        data_steps = [k for k in tool_results if k not in error_only_steps]
        if error_only_steps:
            print(f"  [synthesize] {len(error_only_steps)} step(s) returned errors only: {error_only_steps}")

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
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=synthesis_system,
            messages=[
                {
                    "role": "user",
                    "content": SYNTHESIS_PROMPT.format(
                        query=query,
                        scenario_type=scenario_type,
                        tool_results=results_text,
                    ),
                }
            ],
        )

        text = response.content[0].text
        json_str = _extract_json(text)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[synthesize] JSON parse failed ({e}); retrying with strict JSON instruction...")
            print(f"  [synthesize] Failed response (first 500 chars): {text[:500]!r}")

            # Retry: send the broken response back and ask for clean JSON only
            retry_response = await self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT + SYNTHESIS_SYSTEM_SUPPLEMENT,
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
            )
            retry_text = retry_response.content[0].text
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

        return ImpactAssessment(
            query=AnalystQuery(raw_query=query, scenario_type=st),
            scenario_type=st,
            executive_summary=data.get("executive_summary", ""),
            findings=data.get("findings", []),
            friendly_fire=data.get("friendly_fire", []),
            confidence_summary=confidence_map,
            sources=[SourceReference(name=s) for s in data.get("sources_used", [])],
            recommendations=data.get("recommendations", []),
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


def _compact_tool_results(results: dict[str, Any], max_list: int = 10, max_str: int = 500) -> dict[str, Any]:
    """Recursively compact tool results to fit within synthesis context window.

    Truncates long strings, caps list lengths, and strips raw binary/HTML data.
    Reduces 6MB+ tool dumps to ~50-100KB of meaningful summaries.
    """
    if isinstance(results, dict):
        out = {}
        for k, v in results.items():
            # Skip keys that are typically huge and low-signal
            if k in ("raw_html", "raw_response", "raw_data", "price_history",
                      "historical_data", "chart_data", "curve", "positions"):
                if isinstance(v, list):
                    out[k] = f"[{len(v)} items omitted]"
                else:
                    out[k] = "[omitted]"
            else:
                out[k] = _compact_tool_results(v, max_list, max_str)
        return out
    elif isinstance(results, list):
        if len(results) > max_list:
            compacted = [_compact_tool_results(item, max_list, max_str) for item in results[:max_list]]
            compacted.append(f"... and {len(results) - max_list} more items")
            return compacted
        return [_compact_tool_results(item, max_list, max_str) for item in results]
    elif isinstance(results, str):
        if len(results) > max_str:
            return results[:max_str] + f"... [{len(results) - max_str} chars truncated]"
        return results
    else:
        return results


def _parse_string_tool_call(call_str: str) -> tuple[str, dict[str, Any]]:
    """Parse a Python-style tool call string like 'get_stock_profile(\"SMCI\")' into (name, params).

    Returns (tool_name, {}) if parsing fails, leaving the error to be caught by call_tool.
    """
    import re

    call_str = call_str.strip()
    m = re.match(r'^(\w+)\s*\((.*)\)\s*$', call_str, re.DOTALL)
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
            val: Any = next((v for v in kw_match[1:] if v != ''), '')
            params[key] = int(val) if val.isdigit() else val
    else:
        # Positional args only — try to extract string values
        pos_vals = re.findall(r'"([^"]*?)"|\'([^\']*?)\'|(\d+)', args_str)
        positional = [next(v for v in grp if v != '') for grp in pos_vals]
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
        print("Usage: uv run python -m src.orchestrator.main \"Your question here\"")
        print("\nExample:")
        print('  uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"')
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
        print(f"  {i}. [{f.get('confidence', 'N/A')}] {f.get('category', 'General')}: {f.get('finding', '')}")
    if assessment.friendly_fire:
        print(f"\nFriendly Fire Alerts ({len(assessment.friendly_fire)}):")
        for ff in assessment.friendly_fire:
            print(f"  ⚠ {ff.get('entity', 'Unknown')}: {ff.get('details', '')}")
    if assessment.recommendations:
        print(f"\nRecommendations:")
        for r in assessment.recommendations:
            print(f"  • {r}")


if __name__ == "__main__":
    asyncio.run(main())
