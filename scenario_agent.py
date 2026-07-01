"""
EcoTrace AI - Supply Chain Scenario Simulator Agent

The agent-facing entry point for hypothetical "what if" supply-chain
exploration. Given either:
  (a) a natural-language request ("What happens if we replace air freight
      with sea freight?", "What if Supplier A is replaced by Supplier C?"),
      or
  (b) a structured ScenarioRequest (e.g. built directly from the Streamlit
      Scenario Simulator page's form inputs),

this agent:
  1. Parses/validates the hypothetical into a ScenarioRequest (Pydantic model).
  2. Calls simulation_engine.py to apply the modification and recalculate
     emissions, cost, and operational (lead-time) impact against the
     current baseline.
  3. Uses the LLM to narrate the resulting trade-offs in plain business
     language (the "Explain tradeoffs" step of the required workflow).

The actual numbers (emissions, cost %, lead-time days, recommendation
tier) are always computed deterministically by simulation_engine.py - the
LLM is only used for scenario parsing and narration, never for the math.
This mirrors the exact separation used by optimization_goal_agent.py /
optimization_engine.py for the Goal Optimizer feature, so the two
"what-if" style features in this app behave consistently.
"""
import json
import re
from typing import Any, Optional

from llm_client import chat
from scenario_models import ScenarioRequest, ScenarioType
from simulation_engine import ScenarioResult, run_simulation

VALID_TRANSPORT_MODES = ["air", "sea", "road", "rail", "local"]

SCENARIO_PARSE_SYSTEM_PROMPT = f"""You are the scenario-parsing component of
the Supply Chain Scenario Simulator Agent for EcoTrace AI. Convert the
user's natural-language hypothetical supply-chain question into a JSON
object with these fields (use null/empty defaults for anything not
mentioned):

{{
  "scenario_type": one of ["transport_switch", "supplier_replacement",
      "regional_sourcing", "consolidation", "route_optimization",
      "warehouse_relocation"],
  "target_suppliers": array of supplier name strings mentioned by name
      (e.g. "Supplier A is replaced by Supplier C" -> target_suppliers
      ["Supplier A"]). Empty array if no specific supplier is named (the
      scenario applies portfolio-wide),
  "from_transport_mode": one of {VALID_TRANSPORT_MODES} or null - the mode
      being moved AWAY from (e.g. "replace air freight with sea freight" -> "air"),
  "to_transport_mode": one of {VALID_TRANSPORT_MODES} or null - the mode
      being moved TO (e.g. "...with sea freight" -> "sea". "reduce air
      transport" with no named target -> null, let the engine pick the
      default lower-carbon mode),
  "replacement_supplier": string or null - the supplier being substituted
      IN (e.g. "Supplier A is replaced by Supplier C" -> "Supplier C"),
  "target_region": string or null - a named country/region to source from
      instead (e.g. "source from India instead of Brazil" -> "India";
      target_suppliers in this case should be the suppliers currently in
      "Brazil" if identifiable, otherwise empty),
  "prioritize_local_sourcing": boolean - true for generic "use local
      suppliers" / "move manufacturing closer to customers" requests with
      no specific named region,
  "shipment_frequency": string or null - only if the user names a target
      cadence (e.g. "ship weekly instead" -> "weekly")
}}

Classification guide:
- "replace air freight with sea freight", "switch from air to sea",
  "what if we used rail instead", "reduce air transport" -> transport_switch
- "replace Supplier A with Supplier C", "what if we switched suppliers",
  "drop Supplier X" -> supplier_replacement
- "source from India instead of Brazil", "use local suppliers", "move
  manufacturing closer to customers", "use regional suppliers" -> regional_sourcing
- "consolidate shipments", "ship less often but in bulk" -> consolidation
- "optimize our routes", "use more efficient routing" -> route_optimization
- "relocate our warehouse", "move production closer to customers" (when
  framed as a facility/manufacturing-location change rather than a
  supplier change) -> warehouse_relocation

Return ONLY the JSON object, no commentary, no markdown fences.
"""

NARRATIVE_SYSTEM_PROMPT = """You are the Supply Chain Scenario Simulator
Agent for EcoTrace AI. You are given a user's hypothetical question and a
deterministically-computed scenario simulation result (current emissions,
scenario emissions, % difference, cost impact, operational/lead-time
impact, affected suppliers, and a recommendation tier).

Write a concise, business-friendly explanation (4-7 sentences) that:
  - states the current and scenario emissions and the % difference,
  - explains WHY the change happens (which suppliers/distances/modes drove it),
  - calls out trade-offs honestly: cost impact AND operational impact
    (shipping/lead time), not just the emissions win,
  - ends with the recommendation tier and a one-line justification.

Use ONLY the numbers provided - do not recalculate, round differently, or
invent figures.
"""


def _parse_scenario_from_text(user_request: str, known_suppliers: Optional[list[str]] = None) -> ScenarioRequest:
    """LLM-backed natural-language scenario parser."""
    context = user_request
    if known_suppliers:
        context += f"\n\n(Suppliers currently on file: {', '.join(known_suppliers)})"

    try:
        result = chat(
            messages=[
                {"role": "system", "content": SCENARIO_PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0,
        )
        content = result["content"].strip()
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
        parsed = json.loads(content)
    except Exception as e:  # noqa: BLE001
        print(f"[scenario_agent] Scenario parsing failed, falling back to heuristics: {e}")
        parsed = _heuristic_parse(user_request, known_suppliers)

    parsed.setdefault(
        "scenario_type", _heuristic_parse(user_request, known_suppliers).get("scenario_type", "transport_switch")
    )
    parsed["raw_request"] = user_request
    try:
        return ScenarioRequest(**parsed)
    except Exception as e:  # noqa: BLE001
        print(f"[scenario_agent] Scenario validation failed ({e}), falling back to heuristics.")
        fallback = _heuristic_parse(user_request, known_suppliers)
        fallback["raw_request"] = user_request
        try:
            return ScenarioRequest(**fallback)
        except Exception:
            return ScenarioRequest(scenario_type="transport_switch", raw_request=user_request)


def _extract_mentioned_suppliers(text_lower: str, known_suppliers: Optional[list[str]]) -> list[str]:
    """Return known supplier names that appear (case-insensitively) in the
    request text, in order of first mention - used by the heuristic
    fallback so a named-supplier scenario doesn't silently degrade into a
    portfolio-wide one just because the LLM call failed."""
    if not known_suppliers:
        return []
    found = [(text_lower.find(s.lower()), s) for s in known_suppliers if s.lower() in text_lower]
    found = [f for f in found if f[0] != -1]
    found.sort()
    return [s for _, s in found]


def _heuristic_parse(user_request: str, known_suppliers: Optional[list[str]] = None) -> dict[str, Any]:
    """Keyword-based fallback parser used if the LLM call/JSON parsing
    fails, so the feature still degrades gracefully (no API quota, etc.)
    rather than erroring out entirely."""
    text = (user_request or "").lower()
    mentioned_suppliers = _extract_mentioned_suppliers(text, known_suppliers)

    if "consolidat" in text:
        return {"scenario_type": "consolidation", "target_suppliers": mentioned_suppliers}
    if "route" in text and ("optimiz" in text or "efficien" in text):
        return {"scenario_type": "route_optimization", "target_suppliers": mentioned_suppliers}
    if "warehouse" in text or "closer to customers" in text or "manufacturing" in text:
        return {"scenario_type": "warehouse_relocation", "target_suppliers": mentioned_suppliers}
    if "replace" in text and "supplier" in text:
        # "Supplier A is replaced by Supplier C": of the two suppliers
        # mentioned, the FIRST one named is the one being replaced
        # (target_suppliers); the LAST one named (if a second one is
        # found) is the new replacement_supplier coming in.
        result: dict[str, Any] = {"scenario_type": "supplier_replacement"}
        if mentioned_suppliers:
            result["target_suppliers"] = [mentioned_suppliers[0]]
            if len(mentioned_suppliers) >= 2:
                result["replacement_supplier"] = mentioned_suppliers[-1]
        return result
    if "local supplier" in text or "regional supplier" in text or "source from" in text or "sourcing" in text:
        return {
            "scenario_type": "regional_sourcing",
            "target_suppliers": mentioned_suppliers,
            "prioritize_local_sourcing": "local" in text,
        }

    # Detect which mode(s) are mentioned, in order of first appearance.
    # "ship"/"ocean" are used as a sea-freight proxy, but only as whole
    # words (not as a substring of "shipment(s)", which is unrelated).
    sea_proxy_pattern = r"\b(ship|ocean)\b(?!ment)"
    mode_positions = []
    for mode in VALID_TRANSPORT_MODES:
        idx = text.find(mode)
        if idx == -1 and mode == "sea":
            proxy_match = re.search(sea_proxy_pattern, text)
            idx = proxy_match.start() if proxy_match else -1
        if idx != -1:
            mode_positions.append((idx, mode))
    mode_positions.sort()
    mentioned = [m for _, m in mode_positions]

    if not mentioned:
        return {
            "scenario_type": "transport_switch",
            "target_suppliers": mentioned_suppliers,
            "from_transport_mode": None,
            "to_transport_mode": None,
        }

    # "X instead of Y" / "X rather than Y" means Y is the mode being
    # REPLACED (from_mode) and X is the new target (to_mode) - the
    # opposite of plain left-to-right mention order, so this check takes
    # priority over the generic position-based fallback below.
    replacement_match = re.search(
        r"\b(" + "|".join(VALID_TRANSPORT_MODES) + r").{0,20}\b(instead of|rather than)\b.{0,20}\b("
        + "|".join(VALID_TRANSPORT_MODES) + r")\b",
        text,
    )
    if replacement_match:
        to_mode, from_mode = replacement_match.group(1), replacement_match.group(3)
        return {
            "scenario_type": "transport_switch",
            "target_suppliers": mentioned_suppliers,
            "from_transport_mode": from_mode,
            "to_transport_mode": to_mode,
        }

    # A mode immediately preceded by a "destination" connector word ("to",
    # "with", "using", "via") is the TARGET mode, regardless of mention
    # order - this disambiguates "switching to sea freight" (sea = target)
    # from "switching air to sea" (air = source, sea = target by position).
    to_connectors = ("to ", "with ", "using ", "via ")
    to_mode = next(
        (m for idx, m in mode_positions if any(text[max(0, idx - len(c)):idx] == c for c in to_connectors)),
        None,
    )

    if to_mode:
        from_candidates = [m for m in mentioned if m != to_mode]
        from_mode = from_candidates[0] if from_candidates else None
    elif len(mentioned) >= 2:
        # No connector found but two distinct modes mentioned - fall back
        # to mention order: first = current/from, second = target/to.
        from_mode, to_mode = mentioned[0], mentioned[1]
    else:
        # Only one mode mentioned and no "to X" connector - treat it as the
        # mode being moved AWAY from (e.g. "reduce air transport").
        from_mode, to_mode = mentioned[0], None

    return {
        "scenario_type": "transport_switch",
        "target_suppliers": mentioned_suppliers,
        "from_transport_mode": from_mode,
        "to_transport_mode": to_mode,
    }


def parse_scenario(user_request: str, known_suppliers: Optional[list[str]] = None) -> ScenarioRequest:
    """Public entry point for natural-language scenario parsing (used by AI Chat)."""
    return _parse_scenario_from_text(user_request, known_suppliers)


def build_scenario_from_form(
    scenario_type: ScenarioType,
    target_suppliers: Optional[list[str]] = None,
    from_transport_mode: Optional[str] = None,
    to_transport_mode: Optional[str] = None,
    replacement_supplier: Optional[str] = None,
    target_region: Optional[str] = None,
    prioritize_local_sourcing: bool = False,
    shipment_frequency: Optional[str] = None,
) -> ScenarioRequest:
    """Public entry point for direct construction from the Streamlit
    Scenario Simulator page's form - no LLM call needed since the
    structured inputs are already unambiguous."""
    return ScenarioRequest(
        scenario_type=scenario_type,
        target_suppliers=target_suppliers or [],
        from_transport_mode=from_transport_mode,
        to_transport_mode=to_transport_mode,
        replacement_supplier=replacement_supplier,
        target_region=target_region,
        prioritize_local_sourcing=prioritize_local_sourcing,
        shipment_frequency=shipment_frequency,
    )


def run_scenario(scenario: ScenarioRequest, estimates: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Core orchestration: simulate -> narrate. Returns a dict combining the
    deterministic ScenarioResult with an LLM-generated narrative, ready
    for direct display in Streamlit or as a chat answer.
    """
    result = run_simulation(scenario, estimates)
    narrative = _narrate_result(scenario, result)
    return {
        "scenario": scenario.model_dump(),
        "result": result.to_dict(),
        "narrative": narrative,
    }


def answer_scenario_question(
    user_message: str, estimates: list[dict[str, Any]], known_suppliers: Optional[list[str]] = None
) -> dict[str, Any]:
    """
    Convenience one-shot entry point for chat integration: parse the raw
    NL question, run the simulation, and narrate it - everything the
    Planner Agent needs in a single call.
    """
    scenario = parse_scenario(user_message, known_suppliers)
    return run_scenario(scenario, estimates)


def _narrate_result(scenario: ScenarioRequest, result: ScenarioResult) -> str:
    payload = {
        "scenario": scenario.model_dump(),
        "current_emissions_kg": result.current_emissions_kg,
        "scenario_emissions_kg": result.scenario_emissions_kg,
        "co2e_savings_kg": result.co2e_savings_kg,
        "pct_difference": result.pct_difference,
        "current_leadtime_days": result.current_leadtime_days,
        "scenario_leadtime_days": result.scenario_leadtime_days,
        "leadtime_delta_days": result.leadtime_delta_days,
        "cost_delta_pct": result.cost_delta_pct,
        "recommendation": result.recommendation,
        "recommendation_reason": result.recommendation_reason,
        "affected_suppliers": [vars(s) for s in result.affected_suppliers],
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
        print(f"[scenario_agent] Narrative generation failed: {e}")
        # Deterministic fallback so the feature still works end-to-end even
        # if the LLM call fails (e.g. API quota issues) - same safety net
        # pattern used by optimization_goal_agent._narrate_plan.
        lines = [
            f"Current emissions: {result.current_emissions_kg:,.0f} kg CO2e. "
            f"Scenario emissions: {result.scenario_emissions_kg:,.0f} kg CO2e "
            f"({result.pct_difference:+.1f}%).",
            f"Operational impact: {result.leadtime_delta_days:+.1f} days shipping time. "
            f"Estimated cost impact: {result.cost_delta_pct:+.1f}%.",
            f"Recommendation: {result.recommendation}. {result.recommendation_reason}",
        ]
        lines.extend(result.notes)
        return "\n".join(lines)
