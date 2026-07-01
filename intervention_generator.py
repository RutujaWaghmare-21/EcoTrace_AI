"""
EcoTrace AI - Intervention Generator

Pure-calculation module (no LLM calls) that looks at the current supply
chain data and generates CANDIDATE interventions of every supported type:

  - Transport substitution   (Air -> Sea, Sea -> Rail, Air -> Rail, etc.)
  - Local/regional sourcing  (move sourcing closer to reduce distance)
  - Shipment consolidation   (fewer, larger shipments -> efficiency gain)
  - Supplier replacement     (swap a low-scoring supplier for a higher one)

Each candidate intervention carries an estimated emissions reduction AND
an estimated cost impact, computed via tools/carbon_calculator.py and the
illustrative cost factors in config.py. This module does NOT decide which
interventions to apply or how to combine them - that ranking/selection
logic lives in optimization_engine.py. This module's only job is: given
the current state, what COULD we do, and what would each option cost/save.
"""
from dataclasses import dataclass, field
from typing import Any, Literal

import config
from tools.carbon_calculator import (
    compare_transport_cost_and_emissions,
    estimate_shipment_cost,
    normalize_transport,
)

InterventionType = Literal[
    "transport_switch", "regional_sourcing", "consolidation", "supplier_replacement"
]

# Preferred lower-carbon transport target for each current mode. Each
# current mode maps to the single best next step down the emissions
# ladder (air's biggest win is sea; sea's next step is rail; etc.)
TRANSPORT_DOWNGRADE_PATH = {
    "air": "sea",
    "sea": "rail",
    "road": "rail",
    "rail": "local",
    "local": None,  # already at the bottom of the ladder
}


@dataclass
class Intervention:
    """A single candidate action with its estimated impact.

    Cost is tracked in two parallel forms because different intervention
    types have different natural cost bases (transport switches affect
    transport cost; sourcing/supplier changes affect material cost), and
    percentages computed against different bases are NOT comparable or
    summable:
      - cost_baseline_units / cost_delta_units: absolute relative-cost-unit
        values (transport cost units from tools/carbon_calculator.py, or a
        material-cost proxy for non-transport interventions). These ARE
        comparable and summable across interventions, and are what the
        optimization engine actually ranks/aggregates on.
      - cost_delta_pct: % change relative to THIS intervention's own
        baseline only - useful for displaying "cost increases by X%" in
        the UI for a single intervention, but never sum these across
        interventions of different types.
    """

    type: InterventionType
    title: str
    supplier: str
    co2e_savings_kg: float            # absolute kg CO2e saved if applied
    pct_reduction_of_supplier: float  # % reduction relative to THIS supplier's current emissions
    cost_baseline_units: float        # this intervention's "before" cost, in relative cost units
    cost_delta_units: float           # absolute change in relative cost units (+ = more expensive)
    cost_delta_pct: float             # % change relative to cost_baseline_units (display only)
    one_time_cost_pct: float          # one-time switching cost, % of cost_baseline_units
    tradeoffs: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def effectiveness_ratio(self) -> float:
        """kg CO2e saved per absolute unit of cost increase. Higher is
        better. Interventions that SAVE money (negative cost_delta_units)
        get a large fixed boost since they're strictly better than any
        cost-positive option, regardless of savings size."""
        if self.cost_delta_units <= 0:
            return 1_000_000 + self.co2e_savings_kg
        return self.co2e_savings_kg / self.cost_delta_units


def _generate_transport_switch_candidates(
    estimates: list[dict[str, Any]], preferred_modes: list[str] | None = None
) -> list[Intervention]:
    """For each supplier not already on the lowest-carbon viable mode,
    propose switching to the next rung down the emissions ladder - unless
    the user specified preferred target modes, in which case prefer those."""
    candidates = []
    for e in estimates:
        if "error" in e:
            continue
        current_mode = e["transport"]["transport_mode"]
        target_mode = TRANSPORT_DOWNGRADE_PATH.get(current_mode)

        # If the user named specific preferred transport modes, try those
        # first (as long as they're actually lower-carbon than current).
        if preferred_modes:
            lower_carbon_preferred = [
                m for m in preferred_modes
                if config.TRANSPORT_EMISSION_FACTORS.get(normalize_transport(m), 999)
                < config.TRANSPORT_EMISSION_FACTORS[current_mode]
            ]
            if lower_carbon_preferred:
                target_mode = normalize_transport(lower_carbon_preferred[0])

        if not target_mode or target_mode == current_mode:
            continue

        comparison = compare_transport_cost_and_emissions(
            distance_km=e["transport"]["distance_km"],
            weight_tonnes=e["transport"]["weight_tonnes"],
            from_mode=current_mode,
            to_mode=target_mode,
        )
        savings_kg = comparison["emissions"]["co2e_savings_kg"]
        if savings_kg <= 0:
            continue

        pct_of_supplier = round((savings_kg / e["total_co2e_kg"]) * 100, 1) if e["total_co2e_kg"] else 0.0

        candidates.append(
            Intervention(
                type="transport_switch",
                title=f"Replace {current_mode} freight with {target_mode} freight for {e['supplier']}",
                supplier=e["supplier"],
                co2e_savings_kg=savings_kg,
                pct_reduction_of_supplier=pct_of_supplier,
                cost_baseline_units=comparison["current_cost_units"],
                cost_delta_units=comparison["cost_delta_units"],
                cost_delta_pct=comparison["cost_pct_change"],
                one_time_cost_pct=comparison["one_time_switch_cost_pct"],
                tradeoffs=(
                    f"Switching from {current_mode} to {target_mode} freight typically increases "
                    f"transit lead time and may require new carrier contracts or route planning."
                    if target_mode in ("sea", "rail")
                    else "May affect delivery speed and minimum order quantities."
                ),
                detail=comparison,
            )
        )
    return candidates


def _generate_regional_sourcing_candidates(
    records: list[dict[str, Any]], estimates: list[dict[str, Any]]
) -> list[Intervention]:
    """Propose moving the highest-distance suppliers to a hypothetical
    regional/local alternative: assumes distance drops to a short local
    haul (using the 'local' transport factor) while material cost rises
    by REGIONAL_SOURCING_COST_DELTA_PCT (regional suppliers often cost
    more per unit, but save heavily on transport)."""
    candidates = []
    estimates_by_supplier = {e["supplier"]: e for e in estimates if "error" not in e}

    # Only worth proposing for the longer-haul shipments - a 50km supplier
    # has nothing meaningful to gain from "going local".
    LOCAL_DISTANCE_THRESHOLD_KM = 500

    for record in records:
        supplier = record.get("supplier")
        e = estimates_by_supplier.get(supplier)
        if not e or e["transport"]["distance_km"] < LOCAL_DISTANCE_THRESHOLD_KM:
            continue

        hypothetical_local_distance_km = 150  # assumed regional sourcing radius
        comparison = compare_transport_cost_and_emissions(
            distance_km=e["transport"]["distance_km"],
            weight_tonnes=e["transport"]["weight_tonnes"],
            from_mode=e["transport"]["transport_mode"],
            to_mode="local",
        )
        # Recompute emissions savings using the shorter hypothetical distance
        # rather than the full original distance at "local" mode factor,
        # since regional sourcing changes distance AND mode together.
        from tools.carbon_calculator import estimate_shipment_emissions

        current_transport_co2e = e["transport"]["transport_co2e_kg"]
        proposed_transport = estimate_shipment_emissions(
            hypothetical_local_distance_km, "local", e["transport"]["weight_tonnes"]
        )
        savings_kg = round(current_transport_co2e - proposed_transport["transport_co2e_kg"], 2)
        if savings_kg <= 0:
            continue

        pct_of_supplier = round((savings_kg / e["total_co2e_kg"]) * 100, 1) if e["total_co2e_kg"] else 0.0

        weight_tonnes = e["transport"]["weight_tonnes"]
        material_cost_baseline = config.ASSUMED_MATERIAL_COST_UNITS_PER_TONNE * weight_tonnes
        material_cost_delta_units = round(
            material_cost_baseline * config.REGIONAL_SOURCING_COST_DELTA_PCT, 2
        )
        material_cost_delta_pct = round(config.REGIONAL_SOURCING_COST_DELTA_PCT * 100, 1)

        candidates.append(
            Intervention(
                type="regional_sourcing",
                title=f"Move sourcing for {supplier} closer to manufacturing (regional supplier)",
                supplier=supplier,
                co2e_savings_kg=savings_kg,
                pct_reduction_of_supplier=pct_of_supplier,
                cost_baseline_units=round(material_cost_baseline, 2),
                cost_delta_units=material_cost_delta_units,
                cost_delta_pct=material_cost_delta_pct,
                one_time_cost_pct=round(config.TRANSPORT_SWITCH_ONE_TIME_COST_PCT * 100, 1),
                tradeoffs=(
                    "Regional suppliers often carry a per-unit price premium and may require "
                    "supplier vetting/onboarding; lead times typically improve due to shorter transit."
                ),
                detail={"original_distance_km": e["transport"]["distance_km"],
                        "hypothetical_local_distance_km": hypothetical_local_distance_km},
            )
        )
    return candidates


def _generate_consolidation_candidates(estimates: list[dict[str, Any]]) -> list[Intervention]:
    """Propose shipment consolidation for suppliers with multiple low-weight
    shipments implied by light weight_tonnes values - consolidating into
    fewer, fuller shipments improves transport efficiency. Modeled as a
    flat % reduction in transport emissions (industry rule-of-thumb range),
    with a net cost SAVING from fewer trips."""
    candidates = []
    CONSOLIDATION_EMISSIONS_REDUCTION_PCT = 0.12  # illustrative average efficiency gain
    LIGHT_SHIPMENT_THRESHOLD_TONNES = 5.0  # below this, consolidation is plausible

    for e in estimates:
        if "error" in e:
            continue
        if e["transport"]["weight_tonnes"] >= LIGHT_SHIPMENT_THRESHOLD_TONNES:
            continue  # already a fairly full shipment, less to gain

        savings_kg = round(e["transport"]["transport_co2e_kg"] * CONSOLIDATION_EMISSIONS_REDUCTION_PCT, 2)
        if savings_kg <= 0:
            continue
        pct_of_supplier = round((savings_kg / e["total_co2e_kg"]) * 100, 1) if e["total_co2e_kg"] else 0.0

        transport_cost = estimate_shipment_cost(
            e["transport"]["distance_km"], e["transport"]["transport_mode"], e["transport"]["weight_tonnes"]
        )
        cost_baseline = transport_cost["cost_units"]
        cost_delta_units = round(cost_baseline * config.CONSOLIDATION_COST_DELTA_PCT, 2)  # negative = savings

        candidates.append(
            Intervention(
                type="consolidation",
                title=f"Consolidate shipments for {e['supplier']}",
                supplier=e["supplier"],
                co2e_savings_kg=savings_kg,
                pct_reduction_of_supplier=pct_of_supplier,
                cost_baseline_units=cost_baseline,
                cost_delta_units=cost_delta_units,
                cost_delta_pct=round(config.CONSOLIDATION_COST_DELTA_PCT * 100, 1),
                one_time_cost_pct=0.0,
                tradeoffs=(
                    "Consolidating shipments may increase warehousing/inventory holding costs "
                    "and slightly lengthen the time between deliveries."
                ),
                detail={"current_weight_tonnes": e["transport"]["weight_tonnes"]},
            )
        )
    return candidates


def _generate_supplier_replacement_candidates(
    scored_suppliers: list[dict[str, Any]], estimates: list[dict[str, Any]], score_floor: float = 60.0
) -> list[Intervention]:
    """Propose replacing the lowest-scoring suppliers (below score_floor)
    with a hypothetical supplier matching the portfolio's best-in-class
    score, assuming proportional emissions improvement and a cost premium
    for the more sustainable replacement."""
    candidates = []
    if not scored_suppliers:
        return candidates

    estimates_by_supplier = {e["supplier"]: e for e in estimates if "error" not in e}
    best_score = max(s["score"] for s in scored_suppliers)

    for s in scored_suppliers:
        if s["score"] >= score_floor:
            continue
        e = estimates_by_supplier.get(s["supplier"])
        if not e:
            continue

        # Assume emissions improve proportionally to the score gap closed -
        # a crude but directionally sound proxy in the absence of a named
        # replacement supplier's actual data.
        score_gap = best_score - s["score"]
        improvement_fraction = min(score_gap / 100, 0.5)  # cap at 50% improvement
        savings_kg = round(e["total_co2e_kg"] * improvement_fraction, 2)
        if savings_kg <= 0:
            continue
        pct_of_supplier = round(improvement_fraction * 100, 1)

        weight_tonnes = e["transport"]["weight_tonnes"]
        material_cost_baseline = config.ASSUMED_MATERIAL_COST_UNITS_PER_TONNE * weight_tonnes
        material_cost_delta_units = round(
            material_cost_baseline * config.SUPPLIER_REPLACEMENT_COST_DELTA_PCT, 2
        )

        candidates.append(
            Intervention(
                type="supplier_replacement",
                title=f"Replace {s['supplier']} with a higher-sustainability-score regional supplier",
                supplier=s["supplier"],
                co2e_savings_kg=savings_kg,
                pct_reduction_of_supplier=pct_of_supplier,
                cost_baseline_units=round(material_cost_baseline, 2),
                cost_delta_units=material_cost_delta_units,
                cost_delta_pct=round(config.SUPPLIER_REPLACEMENT_COST_DELTA_PCT * 100, 1),
                one_time_cost_pct=round(config.TRANSPORT_SWITCH_ONE_TIME_COST_PCT * 200, 1),  # higher switching friction
                tradeoffs=(
                    "Supplier switching carries onboarding risk (quality verification, contract "
                    "renegotiation) and sustainable suppliers often charge a premium; benefits "
                    "compound over time as the relationship matures."
                ),
                detail={"current_score": s["score"], "target_score_reference": best_score},
            )
        )
    return candidates


def generate_all_candidates(
    records: list[dict[str, Any]],
    estimates: list[dict[str, Any]],
    scored_suppliers: list[dict[str, Any]],
    preferred_transport_modes: list[str] | None = None,
    supplier_score_floor: float | None = None,
) -> list[Intervention]:
    """
    Generate the full candidate pool across all four intervention types.
    This is what optimization_engine.py selects/ranks from.
    """
    candidates: list[Intervention] = []
    candidates += _generate_transport_switch_candidates(estimates, preferred_transport_modes)
    candidates += _generate_regional_sourcing_candidates(records, estimates)
    candidates += _generate_consolidation_candidates(estimates)
    candidates += _generate_supplier_replacement_candidates(
        scored_suppliers, estimates, score_floor=supplier_score_floor or 60.0
    )
    return candidates
