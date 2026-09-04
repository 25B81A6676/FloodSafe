"""Request/response schemas for the simulation API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SimulationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str | None = Field(
        default=None,
        description="Preset scenario to activate, e.g. 'extreme_flash_flood'.",
    )
    overrides: dict[str, float] | None = Field(
        default=None,
        description="Manual feature overrides keyed by model feature name.",
    )
    merge: bool = Field(
        default=False,
        description="Merge with the currently active overrides instead of replacing them.",
    )
    location_id: str | None = Field(
        default=None,
        description="Optional location to re-assess immediately and return with the response.",
    )


class ContributorOut(BaseModel):
    key: str
    factor: str
    group: str
    impact: str
    icon: str
    value: float | None = None
    unit: str = ""
    display_value: str
    normalized: float | None = None
    weight: float | None = None
    contribution: float
    share_pct: float
    source: str | None = None
    freshness: str | None = None
    simulated: bool = False
    detail: str | None = None
    rationale: str | None = None


class RiskOut(BaseModel):
    """Response contract for a risk assessment."""

    model_config = ConfigDict(extra="allow")

    location_id: str
    risk_score: int = Field(ge=0, le=100)
    risk_score_precise: float
    risk_level: str
    risk_label: str
    risk_color: str | None = None
    confidence: str
    data_quality_score: float
    timestamp: str
    mode: str
    scenario_id: str | None = None
    trend: str
    score_delta: float | None = None
    contributors: list[ContributorOut]
    model: dict[str, Any]
    feature_summary: dict[str, Any]
    disclaimer: str
