from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..geometry import PolygonGeom
from ..provenance import Provenance, ProvenanceKind
from ..units import unit_def


class Distribution(BaseModel):
    """A parameter distribution with explicit unit.

    Adapters materialise samples deterministically from a seed before execution,
    so that every stochastic input is stored explicitly with the run.
    """

    kind: Literal["constant", "normal", "uniform", "triangular", "lognormal", "exponential"]
    unit: str
    value: float | None = None
    mean: float | None = None
    sd: float | None = None
    low: float | None = None
    high: float | None = None
    mode: float | None = None
    clip_low: float | None = None
    clip_high: float | None = None
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        unit_def(v)
        return v

    @model_validator(mode="after")
    def _params(self) -> Distribution:
        need: dict[str, tuple[str, ...]] = {
            "constant": ("value",),
            "normal": ("mean", "sd"),
            "uniform": ("low", "high"),
            "triangular": ("low", "high", "mode"),
            "lognormal": ("mean", "sd"),
            "exponential": ("mean",),
        }
        missing = [p for p in need[self.kind] if getattr(self, p) is None]
        if missing:
            raise ValueError(f"{self.kind} distribution requires {', '.join(missing)}")
        if self.kind == "uniform" and self.low > self.high:  # type: ignore[operator]
            raise ValueError("uniform low must be <= high")
        if self.kind == "triangular" and not (self.low <= self.mode <= self.high):  # type: ignore[operator]
            raise ValueError("triangular requires low <= mode <= high")
        if self.sd is not None and self.sd < 0:
            raise ValueError("sd must be non-negative")
        return self

    @classmethod
    def constant(cls, value: float, unit: str, provenance: Provenance | None = None) -> Distribution:
        kw = {"provenance": provenance} if provenance else {}
        return cls(kind="constant", value=value, unit=unit, **kw)


class NamedPolygon(BaseModel):
    id: str
    name: str
    polygon: PolygonGeom
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))
