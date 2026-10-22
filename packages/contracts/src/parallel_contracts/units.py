"""Explicit units for every physical quantity.

Canonical internal units are SI (metres, seconds, kilograms, ...). Every stored
quantity carries its unit string; conversions go through this table so that a
unit can never be silently assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, field_validator


@dataclass(frozen=True)
class UnitDef:
    dimension: str
    factor: float  # value * factor + offset = value in the SI unit of the dimension (m, s, kg, m/s, ...)
    offset: float = 0.0


UNITS: dict[str, UnitDef] = {
    # length
    "m": UnitDef("length", 1.0),
    "mm": UnitDef("length", 1e-3),
    "cm": UnitDef("length", 1e-2),
    "km": UnitDef("length", 1e3),
    "ft": UnitDef("length", 0.3048),
    "in": UnitDef("length", 0.0254),
    # time
    "s": UnitDef("time", 1.0),
    "min": UnitDef("time", 60.0),
    "h": UnitDef("time", 3600.0),
    "d": UnitDef("time", 86400.0),
    # mass
    "kg": UnitDef("mass", 1.0),
    "g": UnitDef("mass", 1e-3),
    "t": UnitDef("mass", 1e3),
    # speed
    "m/s": UnitDef("speed", 1.0),
    "km/h": UnitDef("speed", 1 / 3.6),
    "mph": UnitDef("speed", 0.44704),
    # acceleration
    "m/s2": UnitDef("acceleration", 1.0),
    "g0": UnitDef("acceleration", 9.80665),
    # area
    "m2": UnitDef("area", 1.0),
    "ha": UnitDef("area", 1e4),
    "km2": UnitDef("area", 1e6),
    # volume
    "m3": UnitDef("volume", 1.0),
    "L": UnitDef("volume", 1e-3),
    "ML": UnitDef("volume", 1e3),
    # flow
    "m3/s": UnitDef("flow", 1.0),
    "L/s": UnitDef("flow", 1e-3),
    "m3/h": UnitDef("flow", 1 / 3600.0),
    "ML/d": UnitDef("flow", 1e3 / 86400.0),
    # rainfall intensity / depth rate
    "mm/h": UnitDef("rate_depth", 1e-3 / 3600.0),
    "mm/min": UnitDef("rate_depth", 1e-3 / 60.0),
    # pressure / stress
    "Pa": UnitDef("pressure", 1.0),
    "kPa": UnitDef("pressure", 1e3),
    "MPa": UnitDef("pressure", 1e6),
    "GPa": UnitDef("pressure", 1e9),
    # temperature
    "K": UnitDef("temperature", 1.0),
    "degC": UnitDef("temperature", 1.0, 273.15),
    # power / energy
    "W": UnitDef("power", 1.0),
    "kW": UnitDef("power", 1e3),
    "MW": UnitDef("power", 1e6),
    "J": UnitDef("energy", 1.0),
    "kJ": UnitDef("energy", 1e3),
    "MJ": UnitDef("energy", 1e6),
    "GJ": UnitDef("energy", 1e9),
    "kWh": UnitDef("energy", 3.6e6),
    "MWh": UnitDef("energy", 3.6e9),
    # heat flux / heat release rate per unit area
    "W/m2": UnitDef("heat_flux", 1.0),
    "kW/m2": UnitDef("heat_flux", 1e3),
    # density
    "kg/m3": UnitDef("density", 1.0),
    # dimensionless
    "1": UnitDef("dimensionless", 1.0),
    "%": UnitDef("dimensionless", 1e-2),
    "count": UnitDef("count", 1.0),
    # angle
    "rad": UnitDef("angle", 1.0),
    "deg": UnitDef("angle", 3.141592653589793 / 180.0),
    # misc
    "1/s": UnitDef("frequency", 1.0),
    "1/h": UnitDef("frequency", 1 / 3600.0),
    "1/m2": UnitDef("persons_per_area", 1.0),
    "persons/s": UnitDef("flow_rate_persons", 1.0),
    "persons/min": UnitDef("flow_rate_persons", 1 / 60.0),
    "veh/h": UnitDef("vehicles_per_time", 1.0),
    "veh": UnitDef("vehicles", 1.0),
    "Ns2/m8": UnitDef("resistance", 1.0),
}


class UnitError(ValueError):
    pass


def unit_def(unit: str) -> UnitDef:
    try:
        return UNITS[unit]
    except KeyError as exc:  # pragma: no cover - message path tested
        raise UnitError(f"Unknown unit '{unit}'. Every quantity must use a registered unit.") from exc


def dimension_of(unit: str) -> str:
    return unit_def(unit).dimension


def to_si(value: float, unit: str) -> float:
    d = unit_def(unit)
    return value * d.factor + d.offset


def from_si(value: float, unit: str) -> float:
    d = unit_def(unit)
    return (value - d.offset) / d.factor


def convert(value: float, from_unit: str, to_unit: str) -> float:
    a, b = unit_def(from_unit), unit_def(to_unit)
    if a.dimension != b.dimension:
        raise UnitError(f"Cannot convert {from_unit} ({a.dimension}) to {to_unit} ({b.dimension}).")
    return from_si(to_si(value, from_unit), to_unit)


def convert_difference(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a *difference* (e.g. a temperature delta), ignoring affine offsets."""
    a, b = unit_def(from_unit), unit_def(to_unit)
    if a.dimension != b.dimension:
        raise UnitError(f"Cannot convert {from_unit} ({a.dimension}) to {to_unit} ({b.dimension}).")
    return value * a.factor / b.factor


class Quantity(BaseModel):
    value: float
    unit: str

    @field_validator("unit")
    @classmethod
    def _known_unit(cls, v: str) -> str:
        unit_def(v)
        return v

    def to(self, unit: str) -> Quantity:
        return Quantity(value=convert(self.value, self.unit, unit), unit=unit)

    @property
    def si(self) -> float:
        return to_si(self.value, self.unit)
