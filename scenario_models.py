"""
EcoTrace AI - Scenario Simulator Model

The structured representation of a hypothetical supply-chain change
("scenario"), used by scenario_agent.py (parsing/orchestration),
simulation_engine.py (applying the modification + recalculating impact),
and the Streamlit "Scenario Simulator" page (direct construction from
form inputs).

Kept in its own module (rather than inside scenario_agent.py) so
simulation_engine.py can import the type for its function signatures
without creating a circular import between the engine and the agent -
the exact same separation pattern used by optimization_models.py /
optimization_engine.py / optimization_goal_agent.py for the Goal
Optimizer feature.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ScenarioType = Literal[
    "transport_switch",      # Air -> Sea, Air -> Rail, Sea -> Rail, etc.
    "supplier_replacement",  # "What if Supplier A is replaced by Supplier C?"
    "regional_sourcing",     # "What if we source from India instead of Brazil?" / local sourcing
    "consolidation",         # "What if we consolidate shipments?"
    "route_optimization",    # "What if we optimize routes?"
    "warehouse_relocation",  # "What if we move manufacturing closer to customers?"
]


class ScenarioRequest(BaseModel):
    """
    Structured hypothetical supply-chain scenario. Every scenario applies
    to one or more suppliers (target_suppliers; empty = "all suppliers" /
    portfolio-wide), and is one of the ScenarioType variants above. The
    fields below are a superset across all scenario types - each type only
    reads the fields relevant to it (see simulation_engine.py).
    """

    scenario_type: ScenarioType = Field(
        ..., description="Which supported scenario category this hypothetical falls into."
    )
    target_suppliers: list[str] = Field(
        default_factory=list,
        description="Suppliers this scenario applies to. Empty list = apply portfolio-wide "
        "(every supplier currently using a mode/region that the scenario affects).",
    )

    # --- transport_switch fields ---
    from_transport_mode: Optional[str] = Field(
        default=None,
        description="Current transport mode to move away from, e.g. 'air'. If omitted, "
        "applies to whichever mode each target supplier is currently using.",
    )
    to_transport_mode: Optional[str] = Field(
        default=None,
        description="Transport mode to switch to, e.g. 'sea', 'rail'.",
    )

    # --- supplier_replacement fields ---
    replacement_supplier: Optional[str] = Field(
        default=None,
        description="Name of the supplier being substituted in, e.g. 'Supplier C' in "
        "'What if Supplier A is replaced by Supplier C?'. Must be one of the suppliers "
        "already present in the supply chain data.",
    )

    # --- regional_sourcing fields ---
    target_region: Optional[str] = Field(
        default=None,
        description="New sourcing country/region, e.g. 'India' in 'source from India instead "
        "of Brazil'. Used to look up an existing supplier already based there, or as a label "
        "for a hypothetical local/regional source if none exists.",
    )
    prioritize_local_sourcing: bool = Field(
        default=False,
        description="True for generic 'use local suppliers' / 'move manufacturing closer to "
        "customers' requests with no specific named region.",
    )

    # --- consolidation / route_optimization fields ---
    shipment_frequency: Optional[str] = Field(
        default=None,
        description="Target shipment frequency/cadence after consolidation, e.g. 'weekly', "
        "'monthly' - informational only, doesn't change the math, but is surfaced in the "
        "explanation for context.",
    )

    raw_request: Optional[str] = Field(
        default=None,
        description="The original natural-language request this scenario was parsed from, if any.",
    )

    @field_validator("target_suppliers", mode="before")
    @classmethod
    def _normalize_supplier_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(s).strip() for s in v if str(s).strip()]

    @field_validator("from_transport_mode", "to_transport_mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v):
        if v is None:
            return None
        v = str(v).strip().lower()
        return v or None

    class Config:
        json_schema_extra = {
            "example": {
                "scenario_type": "transport_switch",
                "target_suppliers": ["Supplier A"],
                "from_transport_mode": "air",
                "to_transport_mode": "sea",
            }
        }
