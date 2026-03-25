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
from src.orchestrator.prompts import DECOMPOSITION_PROMPT, SYNTHESIS_PROMPT, SYSTEM_PROMPT
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

    async def analyze(self, query: str) -> ImpactAssessment:
        """Run the full analysis pipeline for an analyst's question."""
        print(f"\n{'='*60}")
        print(f"QUERY: {query}")
        print(f"{'='*60}\n")

        # Step 1: Decompose the question into a research plan
        print("[1/4] Decomposing question into research plan...")
        plan = await self._decompose(query)
        print(f"  Research plan: {len(plan)} steps")

        # Step 2: Execute the research plan
        print("[2/4] Executing research plan...")
        tool_results = await self._execute_plan(plan)
        print(f"  Collected {len(tool_results)} tool results")

        # Step 3: Synthesize results
        print("[3/4] Synthesizing findings...")
        assessment = await self._synthesize(query, tool_results)

        # Step 4: Format output
        print("[4/4] Formatting output...")
        print(f"\n{'='*60}")
        print("ANALYSIS COMPLETE")
        print(f"{'='*60}\n")

        return assessment

    async def _decompose(self, query: str) -> list[dict[str, Any]]:
        """Use Claude to decompose the question into a research plan."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=2000,
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

    async def _execute_plan(self, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute the research plan, running independent steps in parallel."""
        results: dict[str, Any] = {}
        completed_steps: set[int] = set()

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
                    print(f"  Step {step_num} failed: {result}")
                else:
                    results[f"step_{step_num}"] = result
                    print(f"  Step {step_num} complete: {step.get('description', '')}")

        return results

    async def _execute_step(
        self, step: dict[str, Any], prior_results: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a single research step by calling the specified tools."""
        step_results: dict[str, Any] = {}
        tools = step.get("tools", [])

        for tool_call in tools:
            tool_name = tool_call if isinstance(tool_call, str) else tool_call.get("tool", "")
            params = {} if isinstance(tool_call, str) else tool_call.get("params", {})

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

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
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
