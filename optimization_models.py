"""
EcoTrace AI - Optimization Goal Model

The structured representation of a user's sustainability optimization
goal, used by optimization_goal_agent.py (parsing), optimization_engine.py
(constraint enforcement), and the Streamlit sidebar (direct construction
from form inputs).

Kept in its own module (rather than inside optimization_goal_agent.py) so
optimization_engine.py can import the type for its function signatures
without creating a circular import between the engine and the agent.
"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class OptimizationGoal(BaseModel):
    """
    Structured optimization goal. All fields are optional except
    target_reduction_pct, since every goal needs *some* target to optimize
    toward - if the user doesn't specify one, the parser/UI defaults to a
    reasonable value (see optimization_goal_agent.DEFAULT_TARGET_REDUCTION_PCT).
    """

    target_reduction_pct: float = Field(
        ..., ge=0, le=100,
        description="Target emissions reduction, as a percentage of current total emissions.",
    )
    max_cost_increase_pct: Optional[float] = Field(
        default=None, ge=0,
        description="Maximum acceptable cost increase, as a percentage. None = no ceiling.",
    )
    preferred_transport_modes: list[str] = Field(
        default_factory=list,
        description="Transport modes to prefer when interventions involve a transport switch, "
        "e.g. ['sea', 'rail'].",
    )
    reduce_modes: list[str] = Field(
        default_factory=list,
        description="Transport modes the user explicitly wants to reduce usage of, "
        "e.g. ['air'] for 'minimize air freight'.",
    )
    prioritize_local_sourcing: bool = Field(
        default=False,
        description="Whether the user wants to prioritize local/regional suppliers.",
    )
    min_supplier_score: Optional[float] = Field(
        default=None, ge=0, le=100,
        description="Minimum acceptable supplier sustainability score - interventions that would "
        "leave any selected supplier below this floor should be deprioritized.",
    )
    minimize_operational_change: bool = Field(
        default=False,
        description="Whether the user wants minimal operational disruption (e.g. 'minimal "
        "operational changes', 'preserve delivery times') - biases the engine toward fewer, "
        "higher-impact interventions rather than many small ones.",
    )
    raw_request: Optional[str] = Field(
        default=None,
        description="The original natural-language request this goal was parsed from, if any.",
    )

    @field_validator("preferred_transport_modes", "reduce_modes", mode="before")
    @classmethod
    def _normalize_mode_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(m).strip().lower() for m in v if str(m).strip()]

    class Config:
        json_schema_extra = {
            "example": {
                "target_reduction_pct": 30,
                "max_cost_increase_pct": 5,
                "preferred_transport_modes": ["sea"],
                "reduce_modes": ["air"],
                "prioritize_local_sourcing": False,
                "min_supplier_score": 70,
                "minimize_operational_change": False,
            }
        }
