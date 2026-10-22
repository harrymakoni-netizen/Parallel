"""CrashLens contracts: evidence observations, hypotheses and evidence-fit.

Scene reconstruction (what was recorded) is separated from event
reconstruction (how the incident may have unfolded). Observations are distinct
from interpretations; hypotheses are compared with defined residuals, never
with invented probabilities of truth.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .geometry import Position


class StatementType(StrEnum):
    observation = "observation"  # what was seen/measured
    interpretation = "interpretation"  # analyst reasoning about what it means


class ObservationKind(StrEnum):
    skid_mark = "skid_mark"
    yaw_mark = "yaw_mark"
    debris = "debris"
    final_position = "final_position"
    road_condition = "road_condition"
    visibility = "visibility"
    vehicle_damage = "vehicle_damage"
    scene_measurement = "scene_measurement"
    other = "other"


class ReviewStatus(StrEnum):
    unreviewed = "unreviewed"
    reviewed = "reviewed"
    disputed = "disputed"


class PlaybackSource(StrEnum):
    recorded_video = "recorded_video"
    analyst_illustration = "analyst_illustration"
    engine_trajectory = "engine_trajectory"
    imported_reconstruction = "imported_reconstruction"


PLAYBACK_LABELS = {
    PlaybackSource.recorded_video: "Recorded video",
    PlaybackSource.analyst_illustration: "Analyst-authored illustration (not computed)",
    PlaybackSource.engine_trajectory: "Engine-computed trajectory",
    PlaybackSource.imported_reconstruction: "Imported external reconstruction",
}


class SequenceStep(BaseModel):
    order: int
    description: str
    participant_ids: list[str] = Field(default_factory=list)
    approx_time_s: float | None = None


class HypothesisParameter(BaseModel):
    name: str
    unit: str
    low: float
    high: float
    nominal: float | None = None
    basis: str = Field(description="Why this range: evidence link, published value, or assumption")


class HypothesisContent(BaseModel):
    description: str
    participant_ids: list[str] = Field(default_factory=list, description="Vehicle record / participant ids")
    sequence: list[SequenceStep] = Field(default_factory=list)
    parameters: list[HypothesisParameter] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    analyst_interpretation: str = ""


class TrajectorySample(BaseModel):
    t_s: float
    x: float
    y: float
    heading_deg: float | None = None
    speed_mps: float | None = None


class TrajectoryColumnMapping(BaseModel):
    """Operator-confirmed mapping of an exported text table to trajectory fields.

    Virtual CRASH documents text export of simulation data; the exact columns
    depend on the export configuration, so the operator confirms the mapping.
    """

    vehicle_column: str
    time_column: str
    x_column: str
    y_column: str
    heading_column: str | None = None
    speed_column: str | None = None
    delimiter: str = ","
    length_unit: Literal["m", "ft"] = "m"
    speed_unit: Literal["m/s", "km/h", "mph"] = "m/s"
    heading_unit: Literal["deg", "rad"] = "deg"
    vehicle_id_map: dict[str, str] = Field(
        default_factory=dict, description="exported vehicle label -> vehicle id"
    )


class EvidenceResidual(BaseModel):
    target_id: str
    kind: str
    vehicle_id: str | None
    measured: Position
    modelled: Position | None
    distance_m: float | None
    heading_error_deg: float | None = None
    uncertainty_m: float
    within_uncertainty: bool | None
    note: str | None = None


class EvidenceFit(BaseModel):
    """Defined evidence-fit measure: residuals between modelled and measured targets.

    ``rms_residual_m`` is the root-mean-square of target distances. It is a
    consistency measure for a given model output, not a probability.
    """

    hypothesis_id: str
    source: PlaybackSource
    residuals: list[EvidenceResidual]
    rms_residual_m: float | None
    targets_within_uncertainty: int
    targets_total: int
    definition: str = (
        "Residual = Euclidean distance (m) between the modelled position at the end of the trajectory "
        "(rest targets) or the closest approach (other targets) and the measured target position."
    )
