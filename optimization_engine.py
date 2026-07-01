"""
EcoTrace AI - Optimization Engine

Takes the candidate interventions produced by intervention_generator.py
and an OptimizationGoal (target reduction %, cost ceiling, transport
preferences, sustainability score floor), then:

  1. Ranks interventions by effectiveness (emissions saved per % cost increase).
  2. Greedily selects a combination that satisfies the goal's constraints.
  3. Produces a before/after projection (current emissions -> optimized
     emissions, total reduction %, cumulative cost impact).

This is a deterministic, explainable selection algorithm (no LLM) so the
numbers are reproducible and auditable - the LLM layer (optimization_goal_agent.py)
is reserved for narrating the *reasoning* behind the plan, not computing it.

Ranking algorithm
------------------
Greedy knapsack-style selection: sort candidates by effectiveness_ratio
(kg CO2e saved per percentage-point of cost increase) descending, then walk
the sorted list adding interventions one at a time as long as:
  - the running total cost increase stays under the goal's max_cost_increase_pct
  - we haven't already exceeded the target emissions reduction (no point
    overshooting if cheaper combinations already hit the goal)
  - at most one intervention is selected per supplier (applying two
    interventions to the same supplier - e.g. transport switch AND
    supplier replacement - would double-count savings, since both
    operate on the same shipment's emissions baseline)

This is a heuristic (not a guaranteed global optimum - true multi-
constraint optimization would need integer programming), but it is fast,
transparent, and good enough for advisory recommendations rather than
binding commitments.
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from intervention_generator import Intervention

if TYPE_CHECKING:
    from optimization_models import OptimizationGoal


@dataclass
class OptimizationResult:
    current_emissions_kg: float
    optimized_emissions_kg: float
    total_reduction_pct: float
    total_cost_increase_pct: float
    goal_met: bool
    selected_interventions: list[Intervention] = field(default_factory=list)
    rejected_interventions: list[Intervention] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_emissions_kg": self.current_emissions_kg,
            "optimized_emissions_kg": self.optimized_emissions_kg,
            "total_reduction_pct": self.total_reduction_pct,
            "total_cost_increase_pct": self.total_cost_increase_pct,
            "goal_met": self.goal_met,
            "selected_interventions": [vars(i) for i in self.selected_interventions],
            "rejected_interventions": [vars(i) for i in self.rejected_interventions],
            "notes": self.notes,
        }


def rank_interventions(candidates: list[Intervention]) -> list[Intervention]:
    """Sort candidates by effectiveness_ratio descending - the core
    ranking algorithm requested by the spec. Ties broken by absolute
    co2e_savings_kg so bigger wins surface first among equally
    cost-efficient options."""
    return sorted(
        candidates,
        key=lambda i: (i.effectiveness_ratio, i.co2e_savings_kg),
        reverse=True,
    )


def _filter_by_transport_preference(
    candidates: list[Intervention], goal: "OptimizationGoal"
) -> list[Intervention]:
    """If the user wants to specifically reduce air freight usage (or named
    transport modes to avoid), tag transport_switch interventions that move
    AWAY from those modes for later prioritization in select_optimization_plan.
    We don't hard-filter other intervention types out - just bias selection."""
    if not goal.reduce_modes:
        return candidates
    reduce_set = {m.lower() for m in goal.reduce_modes}

    tagged = []
    for c in candidates:
        current_mode = c.detail.get("emissions", {}).get("current", {}).get("transport_mode")
        if c.type == "transport_switch" and current_mode in reduce_set:
            c.detail = {**c.detail, "_matches_reduce_mode_goal": True}
        tagged.append(c)
    return tagged


def select_optimization_plan(
    current_emissions_kg: float,
    candidates: list[Intervention],
    goal: "OptimizationGoal",
) -> OptimizationResult:
    """
    Core selection algorithm. Greedily builds a plan from ranked candidates
    that respects the goal's cost ceiling, stopping once the target
    reduction is met or candidates are exhausted.
    """
    if current_emissions_kg <= 0:
        return OptimizationResult(
            current_emissions_kg=0,
            optimized_emissions_kg=0,
            total_reduction_pct=0,
            total_cost_increase_pct=0,
            goal_met=False,
            notes=["No emissions data available to optimize against."],
        )

    candidates = _filter_by_transport_preference(candidates, goal)
    ranked = rank_interventions(candidates)

    # Prioritize candidates that match the user's preferred lower-carbon
    # transport modes or "reduce this mode" goal, if specified, via a
    # stable secondary sort on top of the effectiveness ranking.
    preferred_set = {m.lower() for m in (goal.preferred_transport_modes or [])}

    def _matches_preference(i: Intervention) -> bool:
        proposed_mode = i.detail.get("emissions", {}).get("proposed", {}).get("transport_mode")
        return bool(
            (proposed_mode and proposed_mode in preferred_set)
            or i.detail.get("_matches_reduce_mode_goal")
        )

    if preferred_set or goal.reduce_modes:
        ranked = sorted(ranked, key=lambda i: (not _matches_preference(i), -i.effectiveness_ratio))

    selected: list[Intervention] = []
    rejected: list[Intervention] = []
    used_suppliers: set[str] = set()
    notes: list[str] = []

    running_savings_kg = 0.0
    # Cost is tracked as running SUMS of absolute cost units (baseline and
    # delta), not an average of percentages - percentages from different
    # cost bases (transport vs. material) are not summable/averageable in
    # a meaningful way, but absolute relative-cost-units are.
    running_cost_baseline = 0.0
    running_cost_delta = 0.0

    target_savings_kg = current_emissions_kg * (goal.target_reduction_pct / 100)

    for candidate in ranked:
        if candidate.supplier in used_suppliers:
            rejected.append(candidate)
            continue

        projected_baseline = running_cost_baseline + candidate.cost_baseline_units
        projected_delta = running_cost_delta + candidate.cost_delta_units
        projected_cost_increase_pct = (
            round((projected_delta / projected_baseline) * 100, 1) if projected_baseline else 0.0
        )

        if goal.max_cost_increase_pct is not None and projected_cost_increase_pct > goal.max_cost_increase_pct:
            rejected.append(candidate)
            continue

        # Accept the intervention
        selected.append(candidate)
        used_suppliers.add(candidate.supplier)
        running_savings_kg += candidate.co2e_savings_kg
        running_cost_baseline += candidate.cost_baseline_units
        running_cost_delta += candidate.cost_delta_units

        if running_savings_kg >= target_savings_kg:
            notes.append(
                f"Target reduction of {goal.target_reduction_pct}% reached after "
                f"{len(selected)} intervention(s); remaining lower-priority candidates were not needed."
            )
            break

    # Anything not yet evaluated (loop broke early) also counts as "not selected"
    evaluated_ids = {id(c) for c in selected} | {id(c) for c in rejected}
    remaining = [c for c in ranked if id(c) not in evaluated_ids]
    rejected.extend(remaining)

    optimized_emissions_kg = round(max(current_emissions_kg - running_savings_kg, 0), 2)
    total_reduction_pct = (
        round((running_savings_kg / current_emissions_kg) * 100, 1) if current_emissions_kg else 0.0
    )
    total_cost_increase_pct = (
        round((running_cost_delta / running_cost_baseline) * 100, 1) if running_cost_baseline else 0.0
    )

    goal_met = total_reduction_pct >= goal.target_reduction_pct
    if not goal_met and not notes:
        shortfall = round(goal.target_reduction_pct - total_reduction_pct, 1)
        cost_ceiling_str = f"{goal.max_cost_increase_pct}%" if goal.max_cost_increase_pct is not None else "no"
        notes.append(
            f"Could not fully reach the {goal.target_reduction_pct}% target within the "
            f"{cost_ceiling_str} cost ceiling — projected plan reaches "
            f"{total_reduction_pct}% ({shortfall} percentage points short). Consider "
            f"raising the cost ceiling or relaxing other constraints."
        )

    return OptimizationResult(
        current_emissions_kg=round(current_emissions_kg, 2),
        optimized_emissions_kg=optimized_emissions_kg,
        total_reduction_pct=total_reduction_pct,
        total_cost_increase_pct=total_cost_increase_pct,
        goal_met=goal_met,
        selected_interventions=selected,
        rejected_interventions=rejected,
        notes=notes,
    )
