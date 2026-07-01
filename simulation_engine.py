"""
EcoTrace AI - Scenario Simulation Engine

Pure-calculation module (no LLM calls) implementing the core simulation
workflow required by the Scenario Simulator Agent:

  1. Retrieve current supply chain configuration ("baseline state").
  2. Apply the simulated modification (per ScenarioType).
  3. Recalculate emissions (+ cost, + operational/lead-time impact).
  4. Compare against baseline values (the "delta calculator").
  5. Tier the result into a plain-language recommendation (the
     "recommendation engine").

This mirrors the separation already used by the Goal Optimizer feature
(intervention_generator.py + optimization_engine.py are pure math; the LLM
narration lives one layer up in optimization_goal_agent.py) - here,
simulation_engine.py is that pure-math layer, and scenario_agent.py is the
narration/orchestration layer above it. Reuses tools/carbon_calculator.py
and config.py for every number so results stay consistent with the rest
of the app (Analysis page, Goal Optimizer, etc.) rather than introducing
a second, divergent set of emission/cost assumptions.
"""
from dataclasses import dataclass, field
from typing import Any

import config
from scenario_models import ScenarioRequest
from tools.carbon_calculator import (
    estimate_shipment_cost,
    estimate_shipment_emissions,
    estimate_total_for_record,
    normalize_transport,
)

# Lower-carbon transport ladder reused for "what's the natural target mode"
# inference when the user doesn't name one explicitly (e.g. "reduce air
# transport" -> infer sea as the default target). Same ladder used by
# intervention_generator.py, kept independent here so this module has no
# import-time dependency on the Goal Optimizer feature.
TRANSPORT_DOWNGRADE_PATH = {
    "air": "sea",
    "sea": "rail",
    "road": "rail",
    "rail": "local",
    "local": None,
}

LOCAL_SOURCING_HYPOTHETICAL_DISTANCE_KM = 150  # assumed regional sourcing radius
CONSOLIDATION_EMISSIONS_REDUCTION_PCT = 0.12   # same illustrative efficiency gain as intervention_generator.py
LIGHT_SHIPMENT_THRESHOLD_TONNES = 5.0


@dataclass
class SupplierImpact:
    """Per-supplier before/after detail, so the UI/chat can explain exactly
    which suppliers changed and by how much, not just the portfolio total."""

    supplier: str
    baseline_co2e_kg: float
    scenario_co2e_kg: float
    co2e_savings_kg: float
    baseline_transport_mode: str
    scenario_transport_mode: str
    baseline_distance_km: float
    scenario_distance_km: float
    note: str = ""


@dataclass
class ScenarioResult:
    """The full before/after comparison for a simulated scenario - the
    'Agent Outputs' contract: current emissions, scenario emissions,
    % difference, operational impact, cost impact, recommendation."""

    scenario_type: str
    current_emissions_kg: float
    scenario_emissions_kg: float
    co2e_savings_kg: float
    pct_difference: float                 # negative = reduction, positive = increase
    current_leadtime_days: float
    scenario_leadtime_days: float
    leadtime_delta_days: float
    current_cost_units: float
    scenario_cost_units: float
    cost_delta_pct: float
    recommendation: str                    # "Highly recommended" / "Recommended" / "Conditional" / "Not recommended"
    recommendation_reason: str
    affected_suppliers: list[SupplierImpact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_type": self.scenario_type,
            "current_emissions_kg": self.current_emissions_kg,
            "scenario_emissions_kg": self.scenario_emissions_kg,
            "co2e_savings_kg": self.co2e_savings_kg,
            "pct_difference": self.pct_difference,
            "current_leadtime_days": self.current_leadtime_days,
            "scenario_leadtime_days": self.scenario_leadtime_days,
            "leadtime_delta_days": self.leadtime_delta_days,
            "current_cost_units": self.current_cost_units,
            "scenario_cost_units": self.scenario_cost_units,
            "cost_delta_pct": self.cost_delta_pct,
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "affected_suppliers": [vars(s) for s in self.affected_suppliers],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# 1. Baseline state generator
# ---------------------------------------------------------------------------
def build_baseline_state(estimates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Retrieve the current supply chain configuration as a lookup keyed by
    supplier name, from already-computed Carbon Estimation Agent output
    (`estimates` = carbon_estimation_agent.estimate_all(records)). This is
    deliberately a thin re-keying step (not a recomputation) so the
    baseline always matches exactly what the rest of the app already
    shows on the Analysis/Dashboard pages.
    """
    return {e["supplier"]: e for e in estimates if "error" not in e}


def estimate_leadtime_days(distance_km: float, transport_mode: str) -> float:
    """Illustrative lead-time model: fixed overhead (port/customs handling)
    + distance-based transit time, per config.TRANSPORT_LEADTIME_*."""
    mode = normalize_transport(transport_mode)
    per_1000km = config.TRANSPORT_LEADTIME_DAYS_PER_1000KM.get(mode, 1.0)
    fixed = config.TRANSPORT_LEADTIME_FIXED_DAYS.get(mode, 0.5)
    return round(fixed + (distance_km / 1000.0) * per_1000km, 1)


def _portfolio_leadtime_days(estimates: list[dict[str, Any]]) -> float:
    """Portfolio-level lead time is modeled as the SLOWEST shipment in the
    set (the bottleneck supplier determines when a consolidated order is
    ready), consistent with how a buyer would actually experience it -
    rather than an average, which would understate the real wait."""
    days = [
        estimate_leadtime_days(e["transport"]["distance_km"], e["transport"]["transport_mode"])
        for e in estimates
        if "error" not in e
    ]
    return round(max(days), 1) if days else 0.0


def _portfolio_cost_units(estimates: list[dict[str, Any]]) -> float:
    total = 0.0
    for e in estimates:
        if "error" in e:
            continue
        cost = estimate_shipment_cost(
            e["transport"]["distance_km"], e["transport"]["transport_mode"], e["transport"]["weight_tonnes"]
        )
        total += cost["cost_units"]
    return round(total, 2)


# ---------------------------------------------------------------------------
# 2 & 3. Apply the simulated modification + recalculate emissions
# ---------------------------------------------------------------------------
def _resolve_targets(
    scenario: ScenarioRequest, baseline: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve which baseline supplier entries this scenario actually
    touches. Empty target_suppliers = every supplier whose current mode
    matches from_transport_mode (or, if that's also unset, every
    supplier) - i.e. a portfolio-wide change."""
    if scenario.target_suppliers:
        return [baseline[s] for s in scenario.target_suppliers if s in baseline]

    if scenario.from_transport_mode:
        from_mode = normalize_transport(scenario.from_transport_mode)
        return [e for e in baseline.values() if e["transport"]["transport_mode"] == from_mode]

    return list(baseline.values())


def _simulate_transport_switch(
    scenario: ScenarioRequest, targets: list[dict[str, Any]]
) -> tuple[list[SupplierImpact], list[str]]:
    impacts, notes = [], []
    for e in targets:
        current_mode = e["transport"]["transport_mode"]
        distance_km = e["transport"]["distance_km"]
        weight_tonnes = e["transport"]["weight_tonnes"]

        target_mode = scenario.to_transport_mode or TRANSPORT_DOWNGRADE_PATH.get(current_mode)
        if not target_mode:
            notes.append(f"{e['supplier']} is already on the lowest-carbon mode available; no change applied.")
            continue
        target_mode = normalize_transport(target_mode)
        if target_mode == current_mode:
            continue

        new_transport = estimate_shipment_emissions(distance_km, target_mode, weight_tonnes)
        scenario_total_kg = round(e["embedded"]["embedded_co2e_kg"] + new_transport["transport_co2e_kg"], 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=round(e["total_co2e_kg"] - scenario_total_kg, 2),
                baseline_transport_mode=current_mode,
                scenario_transport_mode=target_mode,
                baseline_distance_km=distance_km,
                scenario_distance_km=distance_km,
            )
        )
    return impacts, notes


def _simulate_supplier_replacement(
    scenario: ScenarioRequest, baseline: dict[str, dict[str, Any]], targets: list[dict[str, Any]]
) -> tuple[list[SupplierImpact], list[str]]:
    """'What if Supplier A is replaced by Supplier C?' - the replacement
    supplier must already exist in the data (its real transport mode,
    distance, and weight are used as the post-scenario state for the
    replaced shipment's volume), so the comparison uses real numbers
    rather than an invented hypothetical supplier."""
    impacts, notes = [], []
    replacement = baseline.get(scenario.replacement_supplier) if scenario.replacement_supplier else None

    if scenario.replacement_supplier and not replacement:
        notes.append(
            f"Replacement supplier '{scenario.replacement_supplier}' was not found in the "
            f"current supply chain data, so its real profile could not be used. Add it via "
            f"Upload Data, or this scenario falls back to a generic best-in-class estimate."
        )

    for e in targets:
        if replacement:
            new_distance = replacement["transport"]["distance_km"]
            new_mode = replacement["transport"]["transport_mode"]
            weight_tonnes = e["transport"]["weight_tonnes"]  # keep this supplier's own shipment volume
            new_transport = estimate_shipment_emissions(new_distance, new_mode, weight_tonnes)
            scenario_total_kg = round(e["embedded"]["embedded_co2e_kg"] + new_transport["transport_co2e_kg"], 2)
        else:
            # No real replacement on file - assume a 30% emissions improvement
            # as a directionally-sound placeholder (flagged in notes above).
            new_distance = e["transport"]["distance_km"]
            new_mode = e["transport"]["transport_mode"]
            scenario_total_kg = round(e["total_co2e_kg"] * 0.7, 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=round(e["total_co2e_kg"] - scenario_total_kg, 2),
                baseline_transport_mode=e["transport"]["transport_mode"],
                scenario_transport_mode=new_mode,
                baseline_distance_km=e["transport"]["distance_km"],
                scenario_distance_km=new_distance,
                note=(
                    f"Replaced by {scenario.replacement_supplier}"
                    if replacement
                    else "Replaced by hypothetical best-in-class supplier (estimate)"
                ),
            )
        )
    return impacts, notes


def _simulate_regional_sourcing(
    scenario: ScenarioRequest, baseline: dict[str, dict[str, Any]], targets: list[dict[str, Any]]
) -> tuple[list[SupplierImpact], list[str]]:
    """Covers both 'source from India instead of Brazil' (named target
    region - look up a real supplier already there if one exists) and
    generic 'use local suppliers' / 'move manufacturing closer to
    customers' (assume a short hypothetical local distance)."""
    impacts, notes = [], []

    region_match = None
    if scenario.target_region:
        region_match = next(
            (e for e in baseline.values() if (e.get("country") or "").lower() == scenario.target_region.lower()),
            None,
        )
        if not region_match:
            notes.append(
                f"No existing supplier found in {scenario.target_region} - using an illustrative "
                f"regional-sourcing distance estimate instead of a real supplier's data."
            )

    for e in targets:
        if region_match:
            new_distance = region_match["transport"]["distance_km"]
            new_mode = region_match["transport"]["transport_mode"]
        else:
            new_distance = LOCAL_SOURCING_HYPOTHETICAL_DISTANCE_KM
            new_mode = "local"

        weight_tonnes = e["transport"]["weight_tonnes"]
        new_transport = estimate_shipment_emissions(new_distance, new_mode, weight_tonnes)
        scenario_total_kg = round(e["embedded"]["embedded_co2e_kg"] + new_transport["transport_co2e_kg"], 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=round(e["total_co2e_kg"] - scenario_total_kg, 2),
                baseline_transport_mode=e["transport"]["transport_mode"],
                scenario_transport_mode=new_mode,
                baseline_distance_km=e["transport"]["distance_km"],
                scenario_distance_km=new_distance,
                note=(f"Sourced from {scenario.target_region}" if region_match else "Local/regional sourcing"),
            )
        )
    return impacts, notes


def _simulate_consolidation(targets: list[dict[str, Any]]) -> tuple[list[SupplierImpact], list[str]]:
    """Shipment consolidation: flat efficiency-gain % on transport
    emissions for lighter shipments (same model as intervention_generator's
    consolidation candidates, for consistency across the app)."""
    impacts, notes = [], []
    for e in targets:
        weight_tonnes = e["transport"]["weight_tonnes"]
        if weight_tonnes >= LIGHT_SHIPMENT_THRESHOLD_TONNES:
            notes.append(f"{e['supplier']}'s shipments are already fairly full; limited consolidation upside.")
            continue

        savings_kg = round(e["transport"]["transport_co2e_kg"] * CONSOLIDATION_EMISSIONS_REDUCTION_PCT, 2)
        scenario_total_kg = round(e["total_co2e_kg"] - savings_kg, 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=savings_kg,
                baseline_transport_mode=e["transport"]["transport_mode"],
                scenario_transport_mode=e["transport"]["transport_mode"],
                baseline_distance_km=e["transport"]["distance_km"],
                scenario_distance_km=e["transport"]["distance_km"],
                note="Consolidated shipments",
            )
        )
    return impacts, notes


def _simulate_route_optimization(targets: list[dict[str, Any]]) -> tuple[list[SupplierImpact], list[str]]:
    """Route optimization: assume a flat % reduction in effective distance
    (fewer empty miles, smarter routing) rather than a mode change."""
    impacts, notes = [], []
    for e in targets:
        distance_km = e["transport"]["distance_km"]
        new_distance = round(distance_km * (1 - config.ROUTE_OPTIMIZATION_DISTANCE_REDUCTION_PCT), 1)
        weight_tonnes = e["transport"]["weight_tonnes"]
        mode = e["transport"]["transport_mode"]

        new_transport = estimate_shipment_emissions(new_distance, mode, weight_tonnes)
        scenario_total_kg = round(e["embedded"]["embedded_co2e_kg"] + new_transport["transport_co2e_kg"], 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=round(e["total_co2e_kg"] - scenario_total_kg, 2),
                baseline_transport_mode=mode,
                scenario_transport_mode=mode,
                baseline_distance_km=distance_km,
                scenario_distance_km=new_distance,
                note="Optimized route",
            )
        )
    return impacts, notes


def _simulate_warehouse_relocation(targets: list[dict[str, Any]]) -> tuple[list[SupplierImpact], list[str]]:
    """'Move manufacturing closer to customers' - modeled as a sharper
    distance cut than plain route optimization (it's a structural change,
    not just smarter routing), defaulting transport to road/local since
    shorter final-mile legs are typically trucked rather than flown/shipped."""
    impacts, notes = [], []
    for e in targets:
        distance_km = e["transport"]["distance_km"]
        weight_tonnes = e["transport"]["weight_tonnes"]
        new_distance = min(distance_km, float(LOCAL_SOURCING_HYPOTHETICAL_DISTANCE_KM * 2))
        new_mode = "road" if new_distance > LOCAL_SOURCING_HYPOTHETICAL_DISTANCE_KM else "local"

        new_transport = estimate_shipment_emissions(new_distance, new_mode, weight_tonnes)
        scenario_total_kg = round(e["embedded"]["embedded_co2e_kg"] + new_transport["transport_co2e_kg"], 2)

        impacts.append(
            SupplierImpact(
                supplier=e["supplier"],
                baseline_co2e_kg=e["total_co2e_kg"],
                scenario_co2e_kg=scenario_total_kg,
                co2e_savings_kg=round(e["total_co2e_kg"] - scenario_total_kg, 2),
                baseline_transport_mode=e["transport"]["transport_mode"],
                scenario_transport_mode=new_mode,
                baseline_distance_km=distance_km,
                scenario_distance_km=new_distance,
                note="Manufacturing relocated closer to customers",
            )
        )
    return impacts, notes


_SIMULATORS = {
    "transport_switch": lambda s, b, t: _simulate_transport_switch(s, t),
    "supplier_replacement": lambda s, b, t: _simulate_supplier_replacement(s, b, t),
    "regional_sourcing": lambda s, b, t: _simulate_regional_sourcing(s, b, t),
    "consolidation": lambda s, b, t: _simulate_consolidation(t),
    "route_optimization": lambda s, b, t: _simulate_route_optimization(t),
    "warehouse_relocation": lambda s, b, t: _simulate_warehouse_relocation(t),
}


# ---------------------------------------------------------------------------
# 4. Delta calculator + cost/operational impact
# ---------------------------------------------------------------------------
def _cost_units_for(distance_km: float, mode: str, weight_tonnes: float) -> float:
    return estimate_shipment_cost(distance_km, mode, weight_tonnes)["cost_units"]


def _compute_cost_and_leadtime_deltas(
    impacts: list[SupplierImpact],
    estimates: list[dict[str, Any]],
    scenario_type: str,
) -> tuple[float, float, float, float, float, float]:
    """Returns (current_cost_units, scenario_cost_units, cost_delta_pct,
    current_leadtime_days, scenario_leadtime_days, leadtime_delta_days)
    for the AFFECTED suppliers only (cost/lead-time impact is most
    meaningful scoped to what actually changed, rather than diluted across
    untouched portfolio suppliers)."""
    if not impacts:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    estimates_by_supplier = {e["supplier"]: e for e in estimates if "error" not in e}

    current_cost = 0.0
    scenario_cost = 0.0
    current_leadtimes = []
    scenario_leadtimes = []

    for impact in impacts:
        e = estimates_by_supplier.get(impact.supplier, {})
        weight_tonnes = e.get("transport", {}).get("weight_tonnes", config.DEFAULT_SHIPMENT_WEIGHT_TONNES)

        current_cost += _cost_units_for(
            impact.baseline_distance_km, impact.baseline_transport_mode, weight_tonnes
        )
        scenario_cost += _cost_units_for(
            impact.scenario_distance_km, impact.scenario_transport_mode, weight_tonnes
        )
        current_leadtimes.append(estimate_leadtime_days(impact.baseline_distance_km, impact.baseline_transport_mode))
        scenario_leadtimes.append(estimate_leadtime_days(impact.scenario_distance_km, impact.scenario_transport_mode))

    # Non-transport-cost-driven scenario types apply their own illustrative
    # material-cost / lead-time deltas on top of the transport-cost math,
    # since the underlying intervention isn't purely a transport change.
    if scenario_type == "supplier_replacement":
        scenario_cost += current_cost * config.SUPPLIER_REPLACEMENT_COST_DELTA_PCT
    elif scenario_type == "regional_sourcing":
        scenario_cost += current_cost * config.REGIONAL_SOURCING_COST_DELTA_PCT
    elif scenario_type == "consolidation":
        scenario_cost += current_cost * config.CONSOLIDATION_COST_DELTA_PCT
        scenario_leadtimes = [lt + config.CONSOLIDATION_LEADTIME_DELTA_DAYS for lt in scenario_leadtimes]

    cost_delta_pct = round(((scenario_cost - current_cost) / current_cost) * 100, 1) if current_cost else 0.0
    current_leadtime = round(max(current_leadtimes), 1) if current_leadtimes else 0.0
    scenario_leadtime = round(max(scenario_leadtimes), 1) if scenario_leadtimes else 0.0

    return (
        round(current_cost, 2),
        round(scenario_cost, 2),
        cost_delta_pct,
        current_leadtime,
        scenario_leadtime,
        round(scenario_leadtime - current_leadtime, 1),
    )


# ---------------------------------------------------------------------------
# 5/6. Recommendation engine
# ---------------------------------------------------------------------------
def _recommend(pct_difference: float, cost_delta_pct: float, leadtime_delta_days: float) -> tuple[str, str]:
    """Translate (emissions %, cost %) into a plain-language recommendation
    tier per config.RECOMMENDATION_TIERS, with a one-line reason."""
    reduction_pct = -pct_difference  # pct_difference is negative for a reduction

    if reduction_pct <= 0:
        return (
            "Not recommended",
            f"This scenario increases emissions by {abs(pct_difference)}% with no environmental benefit.",
        )

    tiers = config.RECOMMENDATION_TIERS
    if (
        reduction_pct >= tiers["highly_recommended"]["min_reduction_pct"]
        and cost_delta_pct <= tiers["highly_recommended"]["max_cost_increase_pct"]
    ):
        label = "Highly recommended"
    elif (
        reduction_pct >= tiers["recommended"]["min_reduction_pct"]
        and cost_delta_pct <= tiers["recommended"]["max_cost_increase_pct"]
    ):
        label = "Recommended"
    else:
        label = "Conditional"

    cost_phrase = (
        f"a cost saving of {abs(cost_delta_pct)}%" if cost_delta_pct < 0 else f"a cost increase of {cost_delta_pct}%"
    )
    leadtime_phrase = (
        f"and shipping time changes by {leadtime_delta_days:+.1f} days"
        if leadtime_delta_days
        else "with no meaningful change to shipping time"
    )
    reason = (
        f"Cuts emissions by {reduction_pct}% with {cost_phrase}, {leadtime_phrase}."
        if label != "Conditional"
        else f"Cuts emissions by {reduction_pct}% but carries {cost_phrase} and "
        f"{leadtime_phrase.replace('and ', '')} - worth evaluating against your priorities."
    )
    return label, reason


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_simulation(scenario: ScenarioRequest, estimates: list[dict[str, Any]]) -> ScenarioResult:
    """
    The full required workflow in one call:
      1. (caller already ran Carbon Estimation Agent -> `estimates`)
      2. build_baseline_state()
      3. apply modification (per scenario_type)
      4. recalculate emissions
      5. compare against baseline (deltas)
      6. tier into a recommendation

    Returns a ScenarioResult ready for direct display (Streamlit page,
    chat answer) or further LLM narration (scenario_agent.py).
    """
    baseline = build_baseline_state(estimates)
    current_total_kg = sum(e["total_co2e_kg"] for e in baseline.values())

    if current_total_kg <= 0 or not baseline:
        return ScenarioResult(
            scenario_type=scenario.scenario_type,
            current_emissions_kg=0, scenario_emissions_kg=0, co2e_savings_kg=0, pct_difference=0,
            current_leadtime_days=0, scenario_leadtime_days=0, leadtime_delta_days=0,
            current_cost_units=0, scenario_cost_units=0, cost_delta_pct=0,
            recommendation="Not recommended",
            recommendation_reason="No emissions data available - upload supply chain data first.",
            notes=["No emissions data available - upload supply chain data first."],
        )

    targets = _resolve_targets(scenario, baseline)
    if not targets:
        return ScenarioResult(
            scenario_type=scenario.scenario_type,
            current_emissions_kg=round(current_total_kg, 2), scenario_emissions_kg=round(current_total_kg, 2),
            co2e_savings_kg=0, pct_difference=0,
            current_leadtime_days=_portfolio_leadtime_days(list(baseline.values())),
            scenario_leadtime_days=_portfolio_leadtime_days(list(baseline.values())),
            leadtime_delta_days=0,
            current_cost_units=_portfolio_cost_units(list(baseline.values())),
            scenario_cost_units=_portfolio_cost_units(list(baseline.values())),
            cost_delta_pct=0,
            recommendation="Not recommended",
            recommendation_reason="No suppliers matched this scenario - check the supplier/region/transport "
            "mode named in the request against the suppliers currently on file.",
            notes=["No matching suppliers found for this scenario."],
        )

    simulator = _SIMULATORS.get(scenario.scenario_type)
    if simulator is None:
        raise ValueError(f"Unsupported scenario_type: {scenario.scenario_type}")

    impacts, notes = simulator(scenario, baseline, targets)

    if not impacts:
        notes = notes or ["This scenario produced no change for the targeted suppliers."]
        portfolio_leadtime = _portfolio_leadtime_days(list(baseline.values()))
        portfolio_cost = _portfolio_cost_units(list(baseline.values()))
        return ScenarioResult(
            scenario_type=scenario.scenario_type,
            current_emissions_kg=round(current_total_kg, 2), scenario_emissions_kg=round(current_total_kg, 2),
            co2e_savings_kg=0, pct_difference=0,
            current_leadtime_days=portfolio_leadtime, scenario_leadtime_days=portfolio_leadtime,
            leadtime_delta_days=0,
            current_cost_units=portfolio_cost, scenario_cost_units=portfolio_cost, cost_delta_pct=0,
            recommendation="Not recommended", recommendation_reason=notes[0], notes=notes,
        )

    total_savings_kg = round(sum(i.co2e_savings_kg for i in impacts), 2)
    scenario_total_kg = round(current_total_kg - total_savings_kg, 2)
    pct_difference = round(-(total_savings_kg / current_total_kg) * 100, 1) if current_total_kg else 0.0

    (
        current_cost, scenario_cost, cost_delta_pct,
        current_leadtime, scenario_leadtime, leadtime_delta,
    ) = _compute_cost_and_leadtime_deltas(impacts, estimates, scenario.scenario_type)

    recommendation, reason = _recommend(pct_difference, cost_delta_pct, leadtime_delta)

    return ScenarioResult(
        scenario_type=scenario.scenario_type,
        current_emissions_kg=round(current_total_kg, 2),
        scenario_emissions_kg=max(scenario_total_kg, 0),
        co2e_savings_kg=total_savings_kg,
        pct_difference=pct_difference,
        current_leadtime_days=current_leadtime,
        scenario_leadtime_days=scenario_leadtime,
        leadtime_delta_days=leadtime_delta,
        current_cost_units=current_cost,
        scenario_cost_units=round(scenario_cost, 2),
        cost_delta_pct=cost_delta_pct,
        recommendation=recommendation,
        recommendation_reason=reason,
        affected_suppliers=impacts,
        notes=notes,
    )
