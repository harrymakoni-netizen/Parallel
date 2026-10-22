"""Rigid-body / robotics scene (NVIDIA Isaac Sim).

Photorealistic reconstructions do not contain collision geometry or physical
properties; bodies here declare them explicitly. General scene imports are only
accepted after the verified reference scenario passes on the installation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..geometry import Position
from ..provenance import Provenance, ProvenanceKind


class RigidBody(BaseModel):
    id: str
    shape: Literal["box", "sphere"]
    size: list[float] = Field(min_length=1, max_length=3, description="box: [sx,sy,sz]; sphere: [radius]")
    mass_kg: float = Field(gt=0)
    static_friction: float = Field(default=0.5, ge=0)
    dynamic_friction: float = Field(default=0.5, ge=0)
    restitution: float = Field(default=0.0, ge=0, le=1)
    position: Position
    orientation_wxyz: list[float] = Field(
        default_factory=lambda: [1.0, 0.0, 0.0, 0.0], min_length=4, max_length=4
    )
    linear_velocity: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    provenance: Provenance = Field(default_factory=lambda: Provenance(kind=ProvenanceKind.assumption))


class RigidBodyScene(BaseModel):
    model_kind: Literal["rigid_body"] = "rigid_body"
    meters_per_unit: float = 1.0
    gravity_mps2: float = 9.81
    ground_plane: bool = True
    collision_mesh_asset_ids: list[str] = Field(default_factory=list)
    bodies: list[RigidBody] = Field(min_length=1)
    duration_s: float = Field(gt=0, le=600)
    physics_dt_s: float = Field(default=1 / 60, gt=0)


class RigidBodyControls(BaseModel):
    kind: Literal["rigid_body"] = "rigid_body"
    body_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)
