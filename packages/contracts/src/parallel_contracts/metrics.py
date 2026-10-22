"""Metric definitions and values (spec section 25.5).

No undocumented "risk", "safety" or "efficiency" scores: every metric has a
stable identifier, unit, definition, calculation method, source outputs,
extent, time interval and missing-value rule.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MetricStatus(StrEnum):
    available = "available"
    unattained = "unattained"  # e.g. T100 when not everyone evacuated within the horizon
    unavailable = "unavailable"  # engine did not provide / could not compute
    not_applicable = "not_applicable"


class MetricDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    name: str
    unit: str
    definition: str
    calculation: str
    source_outputs: list[str]
    extent: str
    time_interval: str
    missing_value_rule: str
    discipline: str
    per_entity: bool = False


class MetricValue(BaseModel):
    metric_id: str
    value: float | None
    unit: str
    status: MetricStatus = MetricStatus.available
    entity: str | None = Field(default=None, description="Entity id for per-entity metrics (exit, node, edge, ...)")
    replicate: int | None = None
    stats: dict[str, float] | None = Field(default=None, description="Replicate distribution summary when available")
    note: str | None = None
