"""
EcoTrace AI - Carbon Estimation Agent

Takes structured supplier/shipment records and produces emission estimates
using tools/carbon_calculator.py, plus a natural-language explanation of
why emissions are high/low (reasoning requirement from the spec).
"""
from typing import Any

from llm_client import chat
from tools.carbon_calculator import estimate_total_for_record

EXPLAIN_SYSTEM_PROMPT = """You are the Carbon Estimation Agent for EcoTrace AI.
You are given a list of supplier emission estimates (already calculated).
Write a short (3-5 sentence) plain-language explanation of:
- which suppliers/transport modes are driving the highest emissions, and why
- any notable patterns (e.g. heavy reliance on air freight, long distances)
Be specific and reference actual numbers from the data. Do not invent data
that isn't present. Keep it concise - this will be shown directly to a
business user, not a technical audience.
"""


def estimate_all(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the carbon calculator over every structured record."""
    results = []
    for record in records:
        try:
            results.append(estimate_total_for_record(record))
        except Exception as e:  # noqa: BLE001
            results.append({"supplier": record.get("supplier", "Unknown"), "error": str(e)})
    return results


def explain_estimates(estimates: list[dict[str, Any]]) -> str:
    """Generate a reasoning explanation for why emissions look the way they do."""
    if not estimates:
        return "No emission estimates are available yet — upload supplier data first."

    summary_lines = []
    for e in estimates:
        if "error" in e:
            continue
        summary_lines.append(
            f"- {e['supplier']} ({e.get('country', 'unknown country')}): "
            f"{e['total_co2e_kg']} kg CO2e total "
            f"[transport={e['transport']['transport_mode']}, "
            f"distance={e['transport']['distance_km']}km, "
            f"material={e.get('material', 'n/a')}]"
        )

    if not summary_lines:
        return "Emission estimates could not be calculated from the available data."

    result = chat(
        messages=[
            {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(summary_lines)},
        ],
        temperature=0.3,
    )
    return result["content"]


def aggregate_summary(estimates: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute dashboard-level aggregates: total emissions, top sources, by transport mode."""
    valid = [e for e in estimates if "error" not in e]
    total_co2e = sum(e["total_co2e_kg"] for e in valid)

    by_supplier = sorted(valid, key=lambda e: e["total_co2e_kg"], reverse=True)

    by_transport: dict[str, float] = {}
    for e in valid:
        mode = e["transport"]["transport_mode"]
        by_transport[mode] = by_transport.get(mode, 0) + e["total_co2e_kg"]

    return {
        "total_co2e_kg": round(total_co2e, 2),
        "supplier_count": len(valid),
        "top_emitters": by_supplier[:5],
        "emissions_by_transport_mode": {k: round(v, 2) for k, v in by_transport.items()},
    }
