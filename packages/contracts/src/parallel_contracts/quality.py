"""Quality states (spec section 12.5).

Each state is assessed independently; a successful reconstruction never
implies the others.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class QualityState(StrEnum):
    visual_reconstruction_available = "visual_reconstruction_available"
    scale_established = "scale_established"
    measurements_checked = "measurements_checked"
    domain_model_prepared = "domain_model_prepared"
    model_reviewed = "model_reviewed"
    simulation_integration_verified = "simulation_integration_verified"
    model_calibrated = "model_calibrated"


QUALITY_LABELS: dict[QualityState, str] = {
    QualityState.visual_reconstruction_available: "Visual reconstruction available",
    QualityState.scale_established: "Scale established",
    QualityState.measurements_checked: "Measurements checked",
    QualityState.domain_model_prepared: "Domain model prepared",
    QualityState.model_reviewed: "Model reviewed",
    QualityState.simulation_integration_verified: "Simulation integration verified",
    QualityState.model_calibrated: "Model calibrated against observations",
}


class QualityRecord(BaseModel):
    state: QualityState
    achieved: bool = False
    evidence: str | None = None
    assessed_by: str | None = None
    assessed_at: datetime | None = None


class QualityAssessment(BaseModel):
    records: list[QualityRecord] = Field(
        default_factory=lambda: [QualityRecord(state=s) for s in QualityState]
    )

    def get(self, state: QualityState) -> QualityRecord:
        for r in self.records:
            if r.state == state:
                return r
        rec = QualityRecord(state=state)
        self.records.append(rec)
        return rec

    def achieved(self, state: QualityState) -> bool:
        return self.get(state).achieved

    def mark(self, state: QualityState, evidence: str, by: str | None, at: datetime | None) -> None:
        rec = self.get(state)
        rec.achieved = True
        rec.evidence = evidence
        rec.assessed_by = by
        rec.assessed_at = at
