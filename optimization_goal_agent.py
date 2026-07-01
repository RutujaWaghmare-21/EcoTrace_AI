"""
EcoTrace AI - Sustainability Goal Optimization Agent

The agent-facing entry point for goal-driven optimization. Given either:
  (a) a natural-language request ("Reduce my carbon footprint by 30% while
      keeping additional costs below 5%"), or
  (b) a structured OptimizationGoal (e.g. built directly from the
      Streamlit sidebar form),

this agent:
  1. Parses/validates the goal into an OptimizationGoal (Pydantic model).
  2. Calls intervention_generator.py to enumerate candidate interventions.
  3. Calls optimization_engine.py to rank and select a plan that satisfies
     the goal's constraints.
  4. Uses the LLM to narrate the resulting strategy in plain business
     language, explaining why each intervention was chosen and what
     trade-offs it carries (the "reasoning" requirement).

The actual numbers (emissions, costs, percentages) are always computed
deterministically by optimization_engine.py / intervention_generator.py -
the LLM is only used for goal parsing and narration, never for the math.
"""
import json
import re
from typing import Any, Optional

from llm_client import chat
from optimization_models import OptimizationGoal
from intervention_generator import generate_all_candidates
from optimization_engine import select_optimization_plan, OptimizationResult

DEFAULT_TARGET_REDUCTION_PCT = 20.0  # used when a goal can't be parsed with a specific target

GOAL_PARSE_SYSTEM_PROMPT = """You are the goal-parsing component of the
Sustainability Goal Optimization Agent for EcoTrace AI. Convert the user's
natural-language sustainability request into a JSON object with these
fields (use null/empty defaults for anything not mentioned):

{
  "target_reduction_pct": number (0-100). If the user gives a vague goal
      like "find a plan to reduce emissions with minimal changes" without
      a specific %, use 20 as a reasonable default,
  "max_cost_increase_pct": number or null (e.g. "keeping costs below 5%" -> 5),
  "preferred_transport_modes": array of strings, lower-carbon modes the
      user wants to favor (e.g. "switch to sea freight" -> ["sea"]),
  "reduce_modes": array of strings, modes the user wants to use LESS of
      (e.g. "minimize air freight" -> ["air"]),
  "prioritize_local_sourcing": boolean,
  "min_supplier_score": number or null (e.g. "keep supplier score above 80" -> 80),
  "minimize_operational_change": boolean (true if the user emphasizes
      "minimal changes", "preserve delivery times", "minimal disruption", etc.)
}

Return ONLY the JSON object, no commentary, no markdown fences.
"""

NARRATIVE_SYSTEM_PROMPT = """You are the Sustainability Goal Optimization
Agent for EcoTrace AI. You are given:
  - the user's optimization goal,
  - a deterministically-computed optimization plan (current emissions,
    optimized emissions, selected interventions with exact savings/cost
    numbers, and any notes about whether the goal was fully met).

Write a concise business-friendly strategy summary (5-8 sentences or a
short bulleted list) that:
  - states the current and projected emissions and the % reduction achieved,
  - explains WHY each major intervention was chosen (its effectiveness),
  - calls out trade-offs honestly (cost, lead time, supplier risk),
  - if the goal was NOT fully met within constraints, says so plainly and
    suggests what the user could relax (cost ceiling, target %, etc.)

Use ONLY the numbers provided - do not recalculate or invent figures.
"""


def _parse_goal_from_text(user_request: str) -> OptimizationGoal:
    """LLM-backed natural-language goal parser."""
    try:
        result = chat(
            messages=[
                {"role": "system", "content": GOAL_PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": user_request},
            ],
            temperature=0,
        )
        content = result["content"].strip()
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
        parsed = json.loads(content)
    except Exception as e:  # noqa: BLE001
        print(f"[optimization_goal_agent] Goal parsing failed, using defaults: {e}")
        parsed = {}

    parsed.setdefault("target_reduction_pct", DEFAULT_TARGET_REDUCTION_PCT)
    parsed["raw_request"] = user_request
    try:
        return OptimizationGoal(**parsed)
    except Exception as e:  # noqa: BLE001
        print(f"[optimization_goal_agent] Goal validation failed ({e}), falling back to defaults.")
        return OptimizationGoal(target_reduction_pct=DEFAULT_TARGET_REDUCTION_PCT, raw_request=user_request)


def parse_goal(user_request: str) -> OptimizationGoal:
    """Public entry point for natural-language goal parsing (used by AI Chat)."""
    return _parse_goal_from_text(user_request)


def build_goal_from_form(
    target_reduction_pct: float,
    max_cost_increase_pct: Optional[float] = None,
    preferred_transport_modes: Optional[list[str]] = None,
    prioritize_local_sourcing: bool = False,
    min_supplier_score: Optional[float] = None,
    reduce_modes: Optional[list[str]] = None,
    minimize_operational_change: bool = False,
) -> OptimizationGoal:
    """Public entry point for direct construction from the Streamlit sidebar
    form - no LLM call needed since the structured inputs are already
    unambiguous."""
    return OptimizationGoal(
        target_reduction_pct=target_reduction_pct,
        max_cost_increase_pct=max_cost_increase_pct,
        preferred_transport_modes=preferred_transport_modes or [],
        reduce_modes=reduce_modes or [],
        prioritize_local_sourcing=prioritize_local_sourcing,
        min_supplier_score=min_supplier_score,
        minimize_operational_change=minimize_operational_change,
    )


def generate_optimization_plan(
    goal: OptimizationGoal,
    records: list[dict[str, Any]],
    estimates: list[dict[str, Any]],
    scored_suppliers: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Core orchestration: candidates -> ranked/selected plan -> narrated
    strategy. Returns a dict combining the deterministic OptimizationResult
    with an LLM-generated narrative, ready for direct display in Streamlit.
    """
    current_emissions_kg = sum(e["total_co2e_kg"] for e in estimates if "error" not in e)

    if current_emissions_kg <= 0:
        return {
            "goal": goal.model_dump(),
            "result": OptimizationResult(
                current_emissions_kg=0, optimized_emissions_kg=0,
                total_reduction_pct=0, total_cost_increase_pct=0, goal_met=False,
                notes=["No emissions data available - upload supply chain data first."],
            ).to_dict(),
            "narrative": "No supply chain data is loaded yet, so there's nothing to optimize. "
            "Upload supplier data on the Upload Data page first.",
        }

    candidates = generate_all_candidates(
        records=records,
        estimates=estimates,
        scored_suppliers=scored_suppliers,
        preferred_transport_modes=goal.preferred_transport_modes,
        supplier_score_floor=goal.min_supplier_score,
    )

    result = select_optimization_plan(current_emissions_kg, candidates, goal)
    narrative = _narrate_plan(goal, result)

    return {
        "goal": goal.model_dump(),
        "result": result.to_dict(),
        "narrative": narrative,
    }


def _narrate_plan(goal: OptimizationGoal, result: OptimizationResult) -> str:
    payload = {
        "goal": goal.model_dump(),
        "current_emissions_kg": result.current_emissions_kg,
        "optimized_emissions_kg": result.optimized_emissions_kg,
        "total_reduction_pct": result.total_reduction_pct,
        "total_cost_increase_pct": result.total_cost_increase_pct,
        "goal_met": result.goal_met,
        "selected_interventions": [
            {
                "title": i.title,
                "type": i.type,
                "co2e_savings_kg": i.co2e_savings_kg,
                "cost_delta_pct": i.cost_delta_pct,
                "tradeoffs": i.tradeoffs,
            }
            for i in result.selected_interventions
        ],
        "notes": result.notes,
    }

    try:
        chat_result = chat(
            messages=[
                {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            temperature=0.3,
        )
        return chat_result["content"]
    except Exception as e:  # noqa: BLE001
        print(f"[optimization_goal_agent] Narrative generation failed: {e}")
        # Fallback: build a plain-text summary directly from the numbers,
        # so the feature still works end-to-end even if the LLM call fails
        # (e.g. API quota issues).
        lines = [
            f"Current emissions: {result.current_emissions_kg:,.0f} kg CO2e. "
            f"Projected emissions after optimization: {result.optimized_emissions_kg:,.0f} kg CO2e "
            f"({result.total_reduction_pct}% reduction, {result.total_cost_increase_pct}% cost change).",
        ]
        for i in result.selected_interventions:
            lines.append(f"- {i.title}: saves {i.co2e_savings_kg:,.0f} kg CO2e, cost impact {i.cost_delta_pct}%.")
        lines.extend(result.notes)
        return "\n".join(lines)
