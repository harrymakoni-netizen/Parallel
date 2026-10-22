"""Geomechanical models: continuum (FLAC3D) and discrete-block (3DEC).

Separate schemas because the modelling approaches differ. Properties, loading
and discontinuities require specialist inputs: a visual rock surface is
insufficient, so material properties without measured/imported provenance
are flagged during validation.

Sign convention (Itasca): compressive stresses are negative.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..geometry import BoxGeom, Position
from ..provenance import Provenance


class GeoMaterial(BaseModel):
    id: str
    name: str
    constitutive_model: Literal["elastic", "mohr-coulomb"] = "mohr-coulomb"
    density_kg_m3: float = Field(gt=0)
    youngs_modulus_pa: float = Field(gt=0)
    poisson_ratio: float = Field(gt=0, lt=0.5)
    cohesion_pa: float | None = Field(default=None, ge=0)
    friction_deg: float | None = Field(default=None, ge=0, lt=90)
    dilation_deg: float = 0.0
    tension_pa: float = 0.0
    provenance: Provenance

    @property
    def bulk_modulus_pa(self) -> float:
        return self.youngs_modulus_pa / (3 * (1 - 2 * self.poisson_ratio))

    @property
    def shear_modulus_pa(self) -> float:
        return self.youngs_modulus_pa / (2 * (1 + self.poisson_ratio))


class MaterialRegion(BaseModel):
    material_id: str
    region: BoxGeom


class InSituStress(BaseModel):
    sxx_pa: float
    syy_pa: float
    szz_pa: float
    note: str = "Compression negative (Itasca convention)."
    provenance: Provenance


class ExcavationStage(BaseModel):
    id: str
    name: str
    region: BoxGeom


class MonitoringPoint(BaseModel):
    id: str
    position: Position
    quantity: Literal["displacement", "stress"] = "displacement"


class ContinuumModel(BaseModel):
    model_kind: Literal["geomech_continuum"] = "geomech_continuum"
    extent: BoxGeom
    zone_size_m: float = Field(gt=0)
    materials: list[GeoMaterial] = Field(min_length=1)
    material_regions: list[MaterialRegion] = Field(default_factory=list)
    default_material_id: str
    stress: InSituStress
    boundary: Literal["fixed_base_roller_sides"] = "fixed_base_roller_sides"
    stages: list[ExcavationStage] = Field(default_factory=list)
    monitoring: list[MonitoringPoint] = Field(default_factory=list)


class JointSet(BaseModel):
    id: str
    dip_deg: float = Field(ge=0, le=90)
    dip_direction_deg: float = Field(ge=0, lt=360)
    spacing_m: float = Field(gt=0)
    friction_deg: float = Field(ge=0, lt=90)
    cohesion_pa: float = Field(ge=0)
    tension_pa: float = 0.0
    normal_stiffness_pa_m: float = Field(gt=0)
    shear_stiffness_pa_m: float = Field(gt=0)
    provenance: Provenance


class BlockModel(BaseModel):
    model_kind: Literal["geomech_discrete"] = "geomech_discrete"
    extent: BoxGeom
    block_material: GeoMaterial
    joint_sets: list[JointSet] = Field(min_length=1)
    stress: InSituStress
    stages: list[ExcavationStage] = Field(default_factory=list)
    monitoring: list[MonitoringPoint] = Field(default_factory=list)


class GeomechControls(BaseModel):
    kind: Literal["geomechanics"] = "geomechanics"
    active_stages: list[str] = Field(default_factory=list, description="Excavation stages to apply, in order")
    material_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)
