"""
EcoTrace AI - Carbon Calculator Tool

Pure calculation functions (no LLM calls) for estimating CO2e emissions
from shipment + product data. Used by the Carbon Estimation Agent and
exposed as a callable "tool" for the LLM via function calling.
"""
from typing import Any

import config


def normalize_transport(transport: str) -> str:
    t = (transport or "").strip().lower()
    aliases = {
        "airplane": "air", "plane": "air", "flight": "air",
        "ship": "sea", "ocean": "sea", "container ship": "sea", "boat": "sea",
        "truck": "road", "trucking": "road", "lorry": "road",
        "train": "rail",
        "local delivery": "local", "last mile": "local",
    }
    t = aliases.get(t, t)
    if t not in config.TRANSPORT_EMISSION_FACTORS:
        return "road"  # safe default
    return t


def estimate_shipment_emissions(
    distance_km: float,
    transport: str,
    weight_tonnes: float = None,
) -> dict[str, Any]:
    """
    Estimate transport CO2e for a single shipment using
    kg CO2e = distance_km * weight_tonnes * emission_factor(transport)
    """
    weight_tonnes = weight_tonnes or config.DEFAULT_SHIPMENT_WEIGHT_TONNES
    transport_norm = normalize_transport(transport)
    factor = config.TRANSPORT_EMISSION_FACTORS[transport_norm]

    co2e_kg = round(distance_km * weight_tonnes * factor, 2)

    return {
        "transport_mode": transport_norm,
        "distance_km": distance_km,
        "weight_tonnes": weight_tonnes,
        "emission_factor_kg_co2e_per_tonne_km": factor,
        "transport_co2e_kg": co2e_kg,
        "relative_tier": config.TRANSPORT_RELATIVE_TIER.get(transport_norm, "Unknown"),
    }


def estimate_product_embedded_emissions(
    material: str, weight_kg: float = None
) -> dict[str, Any]:
    """Rough proxy for production/embedded emissions based on product category."""
    weight_kg = weight_kg if weight_kg is not None else (config.DEFAULT_SHIPMENT_WEIGHT_TONNES * 1000)
    key = (material or "").strip().lower()
    factor = config.PRODUCT_CATEGORY_FACTORS.get(key, config.PRODUCT_CATEGORY_FACTORS["default"])
    co2e_kg = round(weight_kg * factor, 2)
    return {
        "material": material,
        "weight_kg": weight_kg,
        "embedded_factor_kg_co2e_per_kg": factor,
        "embedded_co2e_kg": co2e_kg,
    }


def estimate_total_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    Given a structured supplier/shipment record (as produced by the
    Document Extraction Agent), compute transport + embedded emissions
    and a combined total. Missing fields are handled gracefully.
    """
    distance_km = float(record.get("distance_km") or 0)
    transport = record.get("transport", "road")
    weight_tonnes = record.get("weight_tonnes")
    if weight_tonnes is not None:
        weight_tonnes = float(weight_tonnes)
    material = record.get("material", "")

    transport_result = estimate_shipment_emissions(distance_km, transport, weight_tonnes)
    weight_kg = (weight_tonnes or config.DEFAULT_SHIPMENT_WEIGHT_TONNES) * 1000
    embedded_result = estimate_product_embedded_emissions(material, weight_kg)

    total_co2e_kg = round(
        transport_result["transport_co2e_kg"] + embedded_result["embedded_co2e_kg"], 2
    )

    return {
        "supplier": record.get("supplier", "Unknown"),
        "country": record.get("country", "Unknown"),
        "material": material,
        "transport": transport_result,
        "embedded": embedded_result,
        "total_co2e_kg": total_co2e_kg,
    }


def estimate_shipment_cost(
    distance_km: float, transport: str, weight_tonnes: float = None
) -> dict[str, Any]:
    """
    Rough relative transport cost for a shipment using
    cost_units = distance_km * weight_tonnes * cost_factor(transport)

    These are relative cost units (not currency), calibrated so that road
    freight = 1.0x baseline. Useful for comparing the *relative* cost impact
    of switching transport modes, not for absolute budgeting.
    """
    weight_tonnes = weight_tonnes or config.DEFAULT_SHIPMENT_WEIGHT_TONNES
    transport_norm = normalize_transport(transport)
    factor = config.TRANSPORT_COST_FACTORS[transport_norm]
    cost_units = round(distance_km * weight_tonnes * factor, 2)
    return {
        "transport_mode": transport_norm,
        "cost_factor": factor,
        "cost_units": cost_units,
    }


def compare_transport_scenario(
    distance_km: float, weight_tonnes: float, from_mode: str, to_mode: str
) -> dict[str, Any]:
    """'What if we switch from X freight to Y freight?' tool."""
    current = estimate_shipment_emissions(distance_km, from_mode, weight_tonnes)
    proposed = estimate_shipment_emissions(distance_km, to_mode, weight_tonnes)
    savings_kg = round(current["transport_co2e_kg"] - proposed["transport_co2e_kg"], 2)
    pct_reduction = (
        round((savings_kg / current["transport_co2e_kg"]) * 100, 1)
        if current["transport_co2e_kg"] > 0
        else 0
    )
    return {
        "current": current,
        "proposed": proposed,
        "co2e_savings_kg": savings_kg,
        "pct_reduction": pct_reduction,
    }


def compare_transport_cost_and_emissions(
    distance_km: float, weight_tonnes: float, from_mode: str, to_mode: str
) -> dict[str, Any]:
    """
    Combined emissions + cost comparison for a transport-mode switch.
    This is the core calculation the Optimization Engine uses to evaluate
    "Air -> Sea", "Sea -> Rail", etc. interventions: exact emissions math
    plus a relative cost delta, so interventions can be ranked on both axes.
    """
    emissions = compare_transport_scenario(distance_km, weight_tonnes, from_mode, to_mode)
    current_cost = estimate_shipment_cost(distance_km, from_mode, weight_tonnes)
    proposed_cost = estimate_shipment_cost(distance_km, to_mode, weight_tonnes)

    cost_delta_units = round(proposed_cost["cost_units"] - current_cost["cost_units"], 2)
    cost_pct_change = (
        round((cost_delta_units / current_cost["cost_units"]) * 100, 1)
        if current_cost["cost_units"] > 0
        else 0.0
    )
    # One-time switching cost (re-routing, new contracts) added on top,
    # expressed as a % of current transport cost.
    one_time_cost_pct = round(config.TRANSPORT_SWITCH_ONE_TIME_COST_PCT * 100, 1)

    return {
        "emissions": emissions,
        "current_cost_units": current_cost["cost_units"],
        "proposed_cost_units": proposed_cost["cost_units"],
        "cost_delta_units": cost_delta_units,
        "cost_pct_change": cost_pct_change,
        "one_time_switch_cost_pct": one_time_cost_pct,
    }


# --- OpenAI tool schema for direct LLM function calling --------------------
CARBON_CALCULATOR_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "estimate_shipment_emissions",
        "description": (
            "Estimate CO2e emissions in kilograms for a single shipment, "
            "given distance in km, transport mode (air/sea/road/rail/local), "
            "and shipment weight in tonnes. Use this whenever the user asks "
            "about emissions for a specific shipment or transport comparison."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "distance_km": {"type": "number", "description": "Shipment distance in kilometers"},
                "transport": {
                    "type": "string",
                    "enum": ["air", "sea", "road", "rail", "local"],
                    "description": "Mode of transport",
                },
                "weight_tonnes": {
                    "type": "number",
                    "description": "Shipment weight in tonnes (default 1.0 if unknown)",
                },
            },
            "required": ["distance_km", "transport"],
        },
    },
}

SCENARIO_COMPARISON_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_transport_scenario",
        "description": (
            "Compare CO2e emissions between two transport modes for the same "
            "shipment, e.g. 'what if we switch from air to sea freight'. "
            "Returns savings in kg CO2e and percentage reduction."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "distance_km": {"type": "number"},
                "weight_tonnes": {"type": "number"},
                "from_mode": {"type": "string", "enum": ["air", "sea", "road", "rail", "local"]},
                "to_mode": {"type": "string", "enum": ["air", "sea", "road", "rail", "local"]},
            },
            "required": ["distance_km", "from_mode", "to_mode"],
        },
    },
}

COST_EMISSIONS_COMPARISON_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_transport_cost_and_emissions",
        "description": (
            "Compare both CO2e emissions AND relative cost between two "
            "transport modes for the same shipment. Use this when the user "
            "cares about cost trade-offs, not just emissions, e.g. "
            "'reduce emissions while keeping costs low'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "distance_km": {"type": "number"},
                "weight_tonnes": {"type": "number"},
                "from_mode": {"type": "string", "enum": ["air", "sea", "road", "rail", "local"]},
                "to_mode": {"type": "string", "enum": ["air", "sea", "road", "rail", "local"]},
            },
            "required": ["distance_km", "from_mode", "to_mode"],
        },
    },
}
