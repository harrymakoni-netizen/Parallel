"""OpenStudio runner — executed inside the isolated openstudio runtime.

Usage: python -I openstudio_runner.py <input_dir> <output_dir> [energyplus_executable]

Builds an OpenStudio model from Parallel's thermal schema (thermal_input.json):
zones from floor prints, matched/intersected surfaces, windows by
window-to-wall ratio, massless constructions from stated U-values, constant
internal loads and setpoints, ideal loads air systems and design days.
Translates it to an EnergyPlus IDF. If an EnergyPlus executable is supplied,
runs it (design days, or annual with the supplied weather file).
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import openstudio

RSI_RSO = (
    0.17  # combined interior+exterior surface film resistance (m2K/W) subtracted from 1/U for walls/roofs
)
RSI_RSO_FLOOR = 0.21


def constant_schedule(model, name, value):
    s = openstudio.model.ScheduleRuleset(model)
    s.setName(name)
    s.defaultDaySchedule().addValue(openstudio.Time(0, 24, 0, 0), value)
    return s


def massless(model, name, u_value, films):
    r = max(1.0 / u_value - films, 0.001)
    mat = openstudio.model.MasslessOpaqueMaterial(model, "MediumRough", r)
    mat.setName(f"{name} R={r:.3f}")
    layers = openstudio.model.MaterialVector()
    layers.append(mat)
    c = openstudio.model.Construction(model)
    c.setName(name)
    c.setLayers(layers)
    return c


def build(spec):
    model = openstudio.model.Model()
    site = model.getSite()
    site.setLatitude(spec["latitude_deg"])
    site.setLongitude(spec["longitude_deg"])
    site.setTimeZone(spec["time_zone_h"])
    site.setElevation(spec["elevation_m"])
    model.getBuilding().setNorthAxis(spec["north_axis_deg"])
    env = spec["envelope"]
    loads = spec["loads"]
    space_type = openstudio.model.SpaceType(model)
    space_type.setName("Parallel space type")
    always = constant_schedule(model, "Always on (assumption)", 1.0)
    pd = openstudio.model.PeopleDefinition(model)
    pd.setPeopleperSpaceFloorArea(loads["people_per_m2"])
    people = openstudio.model.People(pd)
    people.setSpaceType(space_type)
    people.setNumberofPeopleSchedule(always)
    people.setActivityLevelSchedule(constant_schedule(model, "Activity 120 W (assumption)", 120.0))
    ld = openstudio.model.LightsDefinition(model)
    ld.setWattsperSpaceFloorArea(loads["lighting_w_m2"])
    lights = openstudio.model.Lights(ld)
    lights.setSpaceType(space_type)
    lights.setSchedule(always)
    ed = openstudio.model.ElectricEquipmentDefinition(model)
    ed.setWattsperSpaceFloorArea(loads["equipment_w_m2"])
    eq = openstudio.model.ElectricEquipment(ed)
    eq.setSpaceType(space_type)
    eq.setSchedule(always)
    heat = constant_schedule(model, "Heating setpoint", loads["heating_setpoint_c"])
    cool = constant_schedule(model, "Cooling setpoint", loads["cooling_setpoint_c"])
    spaces = openstudio.model.SpaceVector()
    zones = []
    for z in spec["zones"]:
        ring = z["footprint"]["coordinates"][0][:-1]
        # OpenStudio floor prints must be ordered so the floor faces down: use clockwise order seen from above
        area2 = sum(
            ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring))
        )
        if area2 > 0:
            ring = list(reversed(ring))
        tz = openstudio.model.ThermalZone(model)
        tz.setName(z["name"])
        for k in range(z["num_floors"]):
            pts = openstudio.Point3dVector()
            for x, y, *_ in ring:
                pts.append(openstudio.Point3d(x, y, 0.0))
            sp = openstudio.model.Space.fromFloorPrint(pts, z["floor_to_floor_m"], model)
            if not sp.is_initialized():
                raise ValueError(f"zone {z['id']}: OpenStudio rejected the floor print")
            space = sp.get()
            space.setName(f"{z['name']} floor {k + 1}")
            space.setZOrigin(k * z["floor_to_floor_m"])
            space.setThermalZone(tz)
            space.setSpaceType(space_type)
            spaces.append(space)
        thermostat = openstudio.model.ThermostatSetpointDualSetpoint(model)
        thermostat.setHeatingSetpointTemperatureSchedule(heat)
        thermostat.setCoolingSetpointTemperatureSchedule(cool)
        tz.setThermostatSetpointDualSetpoint(thermostat)
        tz.setUseIdealAirLoads(True)
        zones.append(tz)
    openstudio.model.intersectSurfaces(spaces)
    openstudio.model.matchSurfaces(spaces)
    wall = massless(model, "Exterior wall (stated U)", env["wall_u_w_m2k"], RSI_RSO)
    roof = massless(model, "Roof (stated U)", env["roof_u_w_m2k"], RSI_RSO)
    floor = massless(model, "Ground floor (stated U)", env["floor_u_w_m2k"], RSI_RSO_FLOOR)
    interior = massless(model, "Interior partition", 2.0, 0.26)
    glazing = openstudio.model.SimpleGlazing(model, env["window_u_w_m2k"], env["window_shgc"])
    gl = openstudio.model.MaterialVector()
    gl.append(glazing)
    window = openstudio.model.Construction(model)
    window.setName("Window (stated U, SHGC)")
    window.setLayers(gl)
    n_windows = 0
    for s in model.getSurfaces():
        bc, typ = s.outsideBoundaryCondition(), s.surfaceType()
        if bc == "Outdoors" and typ == "Wall":
            s.setConstruction(wall)
            if (
                env["window_to_wall_ratio"] > 0
                and s.setWindowToWallRatio(env["window_to_wall_ratio"]).is_initialized()
            ):
                n_windows += 1
        elif bc == "Outdoors" and typ == "RoofCeiling":
            s.setConstruction(roof)
        elif bc == "Ground":
            s.setConstruction(floor)
        else:
            s.setConstruction(interior)
    for sub in model.getSubSurfaces():
        sub.setConstruction(window)
    for dd in spec["design_days"]:
        d = openstudio.model.DesignDay(model)
        d.setName(dd["name"])
        d.setMonth(dd["month"])
        d.setDayOfMonth(dd["day"])
        d.setDayType(dd["day_type"])
        d.setMaximumDryBulbTemperature(dd["max_dry_bulb_c"])
        d.setDailyDryBulbTemperatureRange(dd["daily_range_c"])
        d.setHumidityConditionType("WetBulb")
        d.setWetBulbOrDewPointAtMaximumDryBulb(dd["humidity_wet_bulb_c"])
        d.setBarometricPressure(dd["pressure_pa"])
        d.setWindSpeed(dd["wind_speed_mps"])
        d.setSolarModelIndicator("ASHRAEClearSky")
    sc = model.getSimulationControl()
    sc.setRunSimulationforSizingPeriods(bool(spec["design_days"]))
    sc.setRunSimulationforWeatherFileRunPeriods(spec["run"] == "annual")
    for var in (
        "Zone Ideal Loads Supply Air Total Heating Rate",
        "Zone Ideal Loads Supply Air Total Cooling Rate",
        "Zone Ideal Loads Supply Air Total Heating Energy",
        "Zone Ideal Loads Supply Air Total Cooling Energy",
        "Zone Mean Air Temperature",
    ):
        ov = openstudio.model.OutputVariable(var, model)
        ov.setReportingFrequency("Hourly")
    model.getOutputControlFiles().setOutputSQLite(True) if hasattr(model, "getOutputControlFiles") else None
    return model, {
        "zones": len(zones),
        "spaces": len(spaces),
        "surfaces": len(model.getSurfaces()),
        "windows": n_windows,
        "floor_area_m2": model.getBuilding().floorArea(),
    }


def main(inp: Path, out: Path, eplus: str | None) -> int:
    spec = json.loads((inp / "thermal_input.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        model, facts = build(spec)
    except Exception as exc:
        print(f"OpenStudio model error: {exc}")
        return 2
    print("PROGRESS 0.4 OpenStudio model built", flush=True)
    model.save(openstudio.path(str(out / "in.osm")), True)
    ft = openstudio.energyplus.ForwardTranslator()
    ws = ft.translateModel(model)
    errors = [e.logMessage() for e in ft.errors()]
    if errors:
        print("Translation errors: " + "; ".join(errors))
        return 2
    ws.save(openstudio.path(str(out / "in.idf")), True)
    if not any(o.iddObject().name() == "Output:SQLite" for o in ws.objects()):
        with open(out / "in.idf", "a") as f:
            f.write("\nOutput:SQLite,\n    SimpleAndTabular;        !- Option Type\n")
    print("PROGRESS 0.6 IDF written", flush=True)
    result = {
        "engine": "energyplus",
        "openstudio_version": openstudio.openStudioLongVersion(),
        "target_energyplus_version": openstudio.energyPlusVersion(),
        "model": facts,
        "translation_warnings": [w.logMessage() for w in ft.warnings()][:50],
        "energyplus_ran": False,
    }
    if eplus:
        args = [
            eplus,
            "-x",
            "-d",
            str(out / "eplus"),
            "-p",
            "eplus",
            "-s",
            "C",
        ]  # -x: expand HVACTemplate objects
        if spec.get("run") == "annual" and spec.get("weather_path"):
            args += ["-w", spec["weather_path"]]
        args.append(str(out / "in.idf"))
        cp = subprocess.run(args, capture_output=True, text=True)
        (out / "energyplus.log").write_text(cp.stdout + cp.stderr)
        result["energyplus_ran"] = True
        result["energyplus_exit_code"] = cp.returncode
        if cp.returncode != 0:
            print(cp.stdout[-2000:] + cp.stderr[-2000:])
            return 1
    result["wall_time_s"] = round(time.time() - t0, 3)
    (out / "runner_result.json").write_text(json.dumps(result, indent=2))
    print("PROGRESS 1.0 done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None))
