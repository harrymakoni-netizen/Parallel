"""Information provenance (spec section 4.2).

Every important input identifies its origin. AI suggestions remain
"unadopted" until a user explicitly adopts them in the application.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .units import unit_def


class ProvenanceKind(StrEnum):
    measured = "measured"
    imported = "imported"
    assumption = "assumption"
    algorithm_estimate = "algorithm_estimate"
    ai_suggested = "ai_suggested"
    derived = "derived"
    synthetic = "synthetic"  # clearly identified synthetic demonstration material


PROVENANCE_LABELS: dict[ProvenanceKind, str] = {
    ProvenanceKind.measured: "Measured",
    ProvenanceKind.imported: "Imported from a named source",
    ProvenanceKind.assumption: "Entered as an assumption",
    ProvenanceKind.algorithm_estimate: "Estimated by an algorithm",
    ProvenanceKind.ai_suggested: "Suggested by AI",
    ProvenanceKind.derived: "Derived from another recorded value",
    ProvenanceKind.synthetic: "Synthetic demonstration material",
}


class Uncertainty(BaseModel):
    kind: Literal["absolute", "relative", "range", "stddev", "unknown"] = "unknown"
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    note: str | None = None

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str | None) -> str | None:
        if v is not None:
            unit_def(v)
        return v


class Provenance(BaseModel):
    kind: ProvenanceKind
    source: str | None = Field(
        default=None,
        description="Named source: instrument, document, dataset, algorithm id, or AI provider/model.",
    )
    source_ref: str | None = Field(default=None, description="Identifier of the source asset or record.")
    derived_from: list[str] = Field(default_factory=list)
    recorded_by: str | None = None
    recorded_at: datetime | None = None
    uncertainty: Uncertainty | None = None
    note: str | None = None
    adopted_by: str | None = Field(default=None, description="User who explicitly adopted an AI suggestion.")
    adopted_at: datetime | None = None

    @model_validator(mode="after")
    def _adoption_only_for_ai(self) -> Provenance:
        if self.kind != ProvenanceKind.ai_suggested and (self.adopted_by or self.adopted_at):
            raise ValueError("adopted_by/adopted_at only apply to AI-suggested values")
        return self

    @property
    def is_unadopted_ai(self) -> bool:
        return self.kind == ProvenanceKind.ai_suggested and not self.adopted_by


def assumption(note: str, source: str | None = None, **kw: Any) -> Provenance:
    return Provenance(kind=ProvenanceKind.assumption, note=note, source=source, **kw)


def measured(source: str, **kw: Any) -> Provenance:
    return Provenance(kind=ProvenanceKind.measured, source=source, **kw)


def synthetic(note: str, **kw: Any) -> Provenance:
    return Provenance(kind=ProvenanceKind.synthetic, note=note, **kw)


class SourcedQuantity(BaseModel):
    value: float
    unit: str
    provenance: Provenance

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        unit_def(v)
        return v


class SourcedValue(BaseModel):
    """A property value of any JSON type with explicit unit (if physical) and provenance."""

    value: Any
    unit: str | None = None
    provenance: Provenance

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str | None) -> str | None:
        if v is not None:
            unit_def(v)
        return v


class Assumption(BaseModel):
    id: str
    statement: str
    applies_to: list[str] = Field(default_factory=list, description="Paths or object ids the assumption covers.")
    rationale: str | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


def find_unadopted_ai_values(data: Any, path: str = "$") -> list[str]:
    """Walk a JSON-like structure and return the paths of AI-suggested values not yet adopted.

    A provenance object is recognised by a ``kind`` key equal to ``ai_suggested``.
    """
    found: list[str] = []
    if isinstance(data, dict):
        if data.get("kind") == ProvenanceKind.ai_suggested.value and not data.get("adopted_by"):
            found.append(path)
        for k, v in data.items():
            found.extend(find_unadopted_ai_values(v, f"{path}.{k}"))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found.extend(find_unadopted_ai_values(v, f"{path}[{i}]"))
    return found
