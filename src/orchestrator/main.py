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

        results_text = json.dumps(tool_results, indent=2, default=str)
        # Truncate if too long for context window
        if len(results_text) > 50000:
            results_text = results_text[:50000] + "\n... [truncated]"

        synthesis_system = SYSTEM_PROMPT + SYNTHESIS_SYSTEM_SUPPLEMENT
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
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
        except json.JSONDecodeError:
            # Fallback: wrap raw text in assessment
            data = {
                "scenario_type": scenario_type,
                "executive_summary": text[:500],
                "findings": [],
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


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may have markdown code blocks."""
    # Try to find JSON in code blocks
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        return text[start:end].strip()
    # Try to find raw JSON (array or object)
    for char, end_char in [("[", "]"), ("{", "}")]:
        if char in text:
            start = text.index(char)
            # Find the matching closing bracket
            depth = 0
            for i in range(start, len(text)):
                if text[i] == char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                if depth == 0:
                    return text[start : i + 1]
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
