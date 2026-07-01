"""
EcoTrace AI - Planner Agent

The orchestrator. Understands the user's natural-language goal, decides
which sub-agents/tools are needed and in what order, executes that plan,
and synthesizes a final answer. This is the entry point the Streamlit
"AI Chat" page calls.

Design: rather than a rigid rule-based router, the Planner first asks the
LLM to classify intent + produce an ordered task list (the "execution
plan" required by the spec), then this module executes that plan by
calling the appropriate agents, and finally asks the LLM to compose a
natural-language answer grounded in the actual computed results (plus
any relevant RAG context).
"""
import json
import re
from typing import Any

from llm_client import chat
from tools.vector_store import VectorStore
from tools.memory_store import MemoryStore
from agents import carbon_estimation_agent, supplier_risk_agent, optimization_agent
import optimization_goal_agent
import scenario_agent

PLAN_SYSTEM_PROMPT = """You are the Planner Agent for EcoTrace AI, a supply-chain
carbon auditing system. Given a user's request, produce a JSON object:
{
  "intent": one of ["full_audit", "top_emitters", "reduction_target",
                     "scenario_whatif", "find_alternatives", "goal_optimization",
                     "general_question"],
  "needs_rag": boolean (true if answering requires looking up details from
                uploaded documents/reports rather than just the numeric data),
  "subtasks": [ordered list of short strings describing the steps you will take]
}

Use "goal_optimization" specifically when the user states a concrete,
multi-constraint optimization goal to plan toward - e.g. "reduce emissions
by 30% while keeping costs below 5%", "find a plan to cut emissions with
minimal operational changes", "minimize air freight while preserving
delivery times". Use "reduction_target" only for simpler single-axis asks
without a plan-building request (e.g. "how can I reduce emissions?").

Use "scenario_whatif" for a SINGLE concrete hypothetical change the user
wants simulated, even without a numeric target - e.g. "what happens if we
replace air freight with sea freight?", "what if we source from India
instead of Brazil?", "what if Supplier A is replaced by Supplier C?",
"what if we move manufacturing closer to customers?", "what if we
consolidate shipments?", "can we reduce emissions by switching to sea
freight?". This covers transport-mode switches, supplier replacement,
regional/local sourcing, shipment consolidation, route optimization, and
warehouse/manufacturing relocation - any single what-if, not just transport.

Return ONLY the JSON object, no markdown fences, no commentary.
"""

ANSWER_SYSTEM_PROMPT = """You are EcoTrace AI, a sustainability consultant agent.
Answer the user's question using ONLY the computed data and retrieved
document context provided to you below. Be specific with numbers. If
something isn't in the provided data, say so rather than guessing.
Keep the answer focused and business-friendly (not overly technical).
Explain your reasoning briefly where relevant (why emissions are high,
why a recommendation was chosen, trade-offs).
"""


def _make_plan(user_message: str) -> dict[str, Any]:
    result = chat(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )
    content = result["content"].strip()
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"intent": "general_question", "needs_rag": True, "subtasks": ["Answer directly"]}


def run(
    user_message: str,
    structured_records: list[dict[str, Any]],
    vector_store: VectorStore,
    memory: MemoryStore,
) -> dict[str, Any]:
    """
    Main planner entry point.

    structured_records: all supplier/shipment records extracted so far
        (from Document Extraction Agent, accumulated across uploads).
    Returns: {"plan": {...}, "answer": str, "data": {...}} for transparency
        in the UI (so the user can see the agent's reasoning trail).
    """
    plan = _make_plan(user_message)
    records_by_supplier = {r.get("supplier"): r for r in structured_records if r.get("supplier")}

    # Always compute current estimates/scores/recs - cheap and keeps answers grounded
    estimates = carbon_estimation_agent.estimate_all(structured_records) if structured_records else []
    aggregate = carbon_estimation_agent.aggregate_summary(estimates) if estimates else {}
    scored = supplier_risk_agent.score_suppliers(estimates, records_by_supplier) if estimates else []

    data_context_parts = []
    if estimates:
        data_context_parts.append(f"Aggregate summary: {json.dumps(aggregate, default=str)}")
        data_context_parts.append(f"Supplier sustainability scores: {json.dumps(scored, default=str)}")

    # Goal-driven optimization requests get routed through the dedicated
    # Sustainability Goal Optimization Agent, which produces a deterministic
    # ranked plan (not just an LLM-narrated guess) - same engine the Goal
    # Optimizer page's sidebar form uses.
    if plan.get("intent") == "goal_optimization" and estimates:
        goal = optimization_goal_agent.parse_goal(user_message)
        goal_plan = optimization_goal_agent.generate_optimization_plan(
            goal=goal, records=structured_records, estimates=estimates, scored_suppliers=scored
        )
        answer = goal_plan["narrative"]
        memory.append_chat("user", user_message)
        memory.append_chat("assistant", answer)
        memory.save_recommendations(
            [
                {"title": i["title"], "type": i["type"], "co2e_savings_kg": i["co2e_savings_kg"]}
                for i in goal_plan["result"]["selected_interventions"]
            ]
        )
        return {
            "plan": plan,
            "answer": answer,
            "data": {
                "aggregate": aggregate,
                "scored_suppliers": scored,
                "goal_optimization_result": goal_plan["result"],
                "goal": goal_plan["goal"],
                "rag_chunks_used": 0,
            },
        }

    # Scenario what-if questions get routed through the dedicated Supply
    # Chain Scenario Simulator Agent, which produces a deterministic
    # before/after comparison (current vs. scenario emissions, cost impact,
    # operational/lead-time impact, recommendation tier) - not just a
    # free-text tool-calling guess. Same "early return with full structured
    # result" shape as the goal_optimization branch above.
    if plan.get("intent") == "scenario_whatif" and estimates:
        known_suppliers = [s for s in records_by_supplier.keys() if s]
        scenario_run = scenario_agent.answer_scenario_question(user_message, estimates, known_suppliers)
        answer = scenario_run["narrative"]
        memory.append_chat("user", user_message)
        memory.append_chat("assistant", answer)
        memory.save_scenario(
            {
                "scenario_type": scenario_run["scenario"]["scenario_type"],
                "raw_request": user_message,
                "current_emissions_kg": scenario_run["result"]["current_emissions_kg"],
                "scenario_emissions_kg": scenario_run["result"]["scenario_emissions_kg"],
                "pct_difference": scenario_run["result"]["pct_difference"],
                "recommendation": scenario_run["result"]["recommendation"],
            }
        )
        return {
            "plan": plan,
            "answer": answer,
            "data": {
                "aggregate": aggregate,
                "scored_suppliers": scored,
                "scenario_result": scenario_run["result"],
                "scenario": scenario_run["scenario"],
                "rag_chunks_used": 0,
            },
        }

    recommendations = []
    if plan.get("intent") in ("reduction_target", "find_alternatives", "full_audit") and estimates:
        recommendations = optimization_agent.generate_recommendations(estimates)
        data_context_parts.append(f"Recommendations: {json.dumps(recommendations, default=str)}")

    # RAG retrieval if the plan says it's needed (or no structured data exists yet)
    rag_context = []
    if plan.get("needs_rag") or not structured_records:
        rag_results = vector_store.search(user_message)
        rag_context = [r["text"] for r in rag_results]
        if rag_context:
            data_context_parts.append(
                "Relevant excerpts from uploaded documents:\n" + "\n---\n".join(rag_context)
            )

    if not structured_records and not rag_context:
        answer = (
            "I don't have any supply-chain data yet. Please upload supplier "
            "CSV/XLSX files, invoices, or sustainability reports (PDF/TXT) on "
            "the Upload page, then ask me again."
        )
    else:
        full_context = "\n\n".join(data_context_parts) if data_context_parts else "No data available."
        result = chat(
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": f"User question: {user_message}\n\nAvailable data:\n{full_context}"},
            ],
            temperature=0.3,
        )
        answer = result["content"]

    memory.append_chat("user", user_message)
    memory.append_chat("assistant", answer)

    return {
        "plan": plan,
        "answer": answer,
        "data": {
            "aggregate": aggregate,
            "scored_suppliers": scored,
            "recommendations": recommendations,
            "rag_chunks_used": len(rag_context),
        },
    }
