"""
EcoTrace AI - Optimization Agent

Generates concrete recommendations (transport substitutions, regional
sourcing, shipment consolidation, alternate suppliers) ranked by impact,
along with trade-off reasoning. Uses the carbon calculator tool directly
for "what-if" scenario math (so numbers are exact, not LLM-guessed), then
uses the LLM to turn the numeric scenarios into a prioritized, explained
recommendation list.
"""
import json
import re
from typing import Any

from llm_client import chat
from tools.carbon_calculator import compare_transport_scenario, normalize_transport

RECOMMEND_SYSTEM_PROMPT = """You are the Optimization Agent for EcoTrace AI.
You are given supplier emission estimates and pre-calculated "what-if"
transport-switch scenarios (already computed with exact numbers - do not
recalculate or alter them).

Produce a JSON array of recommendations, each with:
- "title" (short action, e.g. "Switch Supplier A from air to sea freight")
- "supplier" (which supplier this applies to, or "general" if portfolio-wide)
- "estimated_co2e_savings_kg" (number, from the provided scenario data)
- "pct_reduction" (number, from the provided scenario data)
- "tradeoffs" (1-2 sentences: what the business gives up, e.g. longer lead
  time, supplier switching cost, minimum order quantities)
- "priority" ("high", "medium", or "low" based on impact size)

Order the array by estimated_co2e_savings_kg descending. Return ONLY the
JSON array, no commentary, no markdown fences.
"""


def _build_transport_switch_scenarios(estimates: list[dict[str, Any]]) -> list[dict]:
    """For every supplier not already on sea/rail/local, compute the
    sea-freight alternative as the default lower-carbon suggestion."""
    scenarios = []
    for e in estimates:
        if "error" in e:
            continue
        current_mode = e["transport"]["transport_mode"]
        if current_mode in ("sea", "rail", "local"):
            continue  # already low-carbon, skip switch suggestion

        target_mode = "sea" if current_mode == "air" else "rail"
        scenario = compare_transport_scenario(
            distance_km=e["transport"]["distance_km"],
            weight_tonnes=e["transport"]["weight_tonnes"],
            from_mode=current_mode,
            to_mode=target_mode,
        )
        scenarios.append(
            {
                "supplier": e["supplier"],
                "current_mode": current_mode,
                "proposed_mode": target_mode,
                **scenario,
            }
        )
    return sorted(scenarios, key=lambda s: s["co2e_savings_kg"], reverse=True)


def generate_recommendations(estimates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenarios = _build_transport_switch_scenarios(estimates)

    if not scenarios:
        return [
            {
                "title": "No immediate transport-mode switches available",
                "supplier": "general",
                "estimated_co2e_savings_kg": 0,
                "pct_reduction": 0,
                "tradeoffs": "All shipments already use lower-carbon transport modes (sea/rail/local).",
                "priority": "low",
            }
        ]

    payload = json.dumps(scenarios, default=str)
    result = chat(
        messages=[
            {"role": "system", "content": RECOMMEND_SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
        temperature=0.3,
    )
    content = result["content"].strip()
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        recs = json.loads(content)
        if isinstance(recs, list):
            return recs
    except json.JSONDecodeError:
        pass

    # Fallback: build recommendations directly from scenarios without the LLM polish
    return [
        {
            "title": f"Switch {s['supplier']} from {s['current_mode']} to {s['proposed_mode']} freight",
            "supplier": s["supplier"],
            "estimated_co2e_savings_kg": s["co2e_savings_kg"],
            "pct_reduction": s["pct_reduction"],
            "tradeoffs": "May increase transit lead time; verify route availability with supplier.",
            "priority": "high" if s["co2e_savings_kg"] > 0 else "low",
        }
        for s in scenarios
    ]


def answer_whatif_question(question: str, estimates: list[dict[str, Any]]) -> str:
    """
    Free-form 'what if' Q&A, e.g. 'what happens if we switch from air to
    sea freight for Supplier A?'. Lets the LLM call the comparison tool
    directly via function-calling rather than us guessing intent in code.
    """
    from tools.carbon_calculator import SCENARIO_COMPARISON_TOOL_SCHEMA

    context_lines = [
        f"- {e['supplier']}: transport={e['transport']['transport_mode']}, "
        f"distance_km={e['transport']['distance_km']}, "
        f"weight_tonnes={e['transport']['weight_tonnes']}, "
        f"current_co2e_kg={e['total_co2e_kg']}"
        for e in estimates
        if "error" not in e
    ]
    system = (
        "You are the Optimization Agent for EcoTrace AI. Use the "
        "compare_transport_scenario tool to answer what-if questions about "
        "transport mode changes with exact numbers. Here is the current "
        "supplier data you can reference:\n" + "\n".join(context_lines)
    )

    def executor(name, args):
        if name == "compare_transport_scenario":
            return compare_transport_scenario(
                distance_km=args.get("distance_km"),
                weight_tonnes=args.get("weight_tonnes", 1.0),
                from_mode=normalize_transport(args.get("from_mode", "air")),
                to_mode=normalize_transport(args.get("to_mode", "sea")),
            )
        return {"error": f"Unknown tool {name}"}

    result = chat(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
        tools=[SCENARIO_COMPARISON_TOOL_SCHEMA],
        tool_executor=executor,
        temperature=0.3,
    )
    return result["content"]
