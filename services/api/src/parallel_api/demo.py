"""Demonstration package (spec section 31).

Builds five prepared projects through the real API, in process, as a demo
presenter would: scenes, prepared models, scenarios, real engine runs (stored
and labelled as saved results), comparisons, reports, source information and
presenter notes. Nothing is precomputed or imitated: every result comes from
a recorded run on this installation. All source material is synthetic and
labelled as such.

    python -m parallel_api.demo seed            # create the demonstration projects that are missing
    python -m parallel_api.demo seed --only mine_evacuation
    python -m parallel_api.demo reset           # delete demonstration projects (demo_key set) and seed again
    python -m parallel_api.demo reset --no-seed # delete only
    python -m parallel_api.demo status

Reset deletes only projects carrying a demo_key; other projects are never touched.
Runs execute in this process (task_always_eager), so seeding needs the engine runtimes.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import secrets
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("parallel.demo")

DEMO_ORG_NAME, DEMO_ORG_SLUG = "Parallel Demonstrations", "parallel-demonstrations"
PRESENTER_EMAIL, REVIEWER_EMAIL = "presenter@parallel-demo.example", "reviewer@parallel-demo.example"
DEMO_KEYS = ("mine_evacuation", "crashlens", "construction", "urban_planning", "urban_drainage")
SYN = {"kind": "synthetic", "note": "Synthetic demonstration material created for Parallel; not a real site."}


class DemoError(RuntimeError):
    pass


class Api:
    """Calls the real API in process with the presenter's token."""

    def __init__(self, client: Any, token: str):
        self.client, self.token = client, token

    def call(self, method: str, path: str, expect: tuple[int, ...] = (200, 201), **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", **kw.pop("headers", {})}
        r = self.client.request(method, f"/api/v1{path}", headers=headers, **kw)
        if r.status_code not in expect:
            raise DemoError(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.call("POST", path, **kw)


def _saved_label(name: str) -> str:
    return f"Saved result: {name} (recorded {datetime.now(UTC):%Y-%m-%d})"


# ---------------------------------------------------------------- shared building blocks
def ensure_org_and_users() -> dict[str, Any]:
    from sqlalchemy import select

    from parallel_core.db import session_scope
    from parallel_core.models import Organisation, OrganisationMembership, User
    from parallel_core.security import hash_password, issue_token

    password = os.environ.get("PARALLEL_DEMO_PASSWORD")
    created_password = None
    with session_scope() as db:
        org = db.scalar(select(Organisation).where(Organisation.slug == DEMO_ORG_SLUG))
        if org is None:
            org = Organisation(
                name=DEMO_ORG_NAME,
                slug=DEMO_ORG_SLUG,
                settings={"allow_external_ai": True, "allow_case_media_to_ai": False},
            )
            db.add(org)
            db.flush()
        users = {}
        for email, name, role in (
            (PRESENTER_EMAIL, "Demo presenter", "admin"),
            (REVIEWER_EMAIL, "Demo reviewer", "member"),
        ):
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                pw = password or created_password or secrets.token_urlsafe(12)
                created_password = None if password else pw
                u = User(email=email, display_name=name, password_hash=hash_password(pw))
                db.add(u)
                db.flush()
            if not db.scalar(
                select(OrganisationMembership).where(
                    OrganisationMembership.user_id == u.id, OrganisationMembership.organisation_id == org.id
                )
            ):
                db.add(OrganisationMembership(organisation_id=org.id, user_id=u.id, role=role))
            users[email] = u.id
        return {
            "org_id": org.id,
            "presenter_id": users[PRESENTER_EMAIL],
            "reviewer_id": users[REVIEWER_EMAIL],
            "token": issue_token(users[PRESENTER_EMAIL]),
            "new_password": created_password,
        }


def new_project(api: Api, ctx: dict, key: str, **body: Any) -> str:
    from parallel_core.db import session_scope
    from parallel_core.models import Project

    p = api.post("/projects", json={"organisation_id": ctx["org_id"], **body})
    with session_scope() as db:  # marked at once, so even a partial seed can be reset
        db.get(Project, p["id"]).demo_key = key  # type: ignore[union-attr]
    api.post(f"/projects/{p['id']}/members", json={"user_id": ctx["reviewer_id"], "role": "reviewer"})
    return p["id"]


def upload_text(api: Api, pid: str, name: str, text: str) -> str:
    return api.post(f"/projects/{pid}/assets", files={"file": (name, text.encode(), "text/markdown")})["id"]


def plan_scene(api: Api, pid: str, name: str, objects: list[dict], assumptions: list[dict], note: str) -> str:
    """A prepared plan scene (no capture): synthetic plan geometry as prepared-model objects."""
    rev = api.post(f"/projects/{pid}/scenes", json={"name": name, "description": note})
    content = api.get(f"/scene-revisions/{rev['id']}")["content"]
    content.update(spatial_objects=objects, assumptions=assumptions, provenance=SYN)
    rev = api.post(
        f"/scenes/{rev['scene_id']}/revisions",
        json={"content": content, "message": note, "base_revision_id": rev["id"]},
    )
    return api.post(f"/scene-revisions/{rev['id']}/publish")["id"]


def obj(oid: str, name: str, kind: str, geometry: dict, layer: str = "prepared", **props: Any) -> dict:
    return {
        "id": oid,
        "name": name,
        "kind": kind,
        "layer": layer,
        "geometry": geometry,
        "belongs_to": "prepared_model",
        "provenance": SYN,
        "properties": {k: {"value": v, "unit": u, "provenance": SYN} for k, (v, u) in props.items()},
    }


def poly(p: Any) -> dict:
    return {"type": "Polygon", "coordinates": p.coordinates if hasattr(p, "coordinates") else p}


def assumption(aid: str, statement: str, rationale: str, applies_to: list[str] | None = None) -> dict:
    return {
        "id": aid,
        "statement": statement,
        "rationale": rationale,
        "applies_to": applies_to or [],
        "provenance": {"kind": "assumption", "note": rationale},
    }


def scenario(api: Api, pid: str, dm: str, name: str, content: dict) -> str:
    return api.post(
        f"/projects/{pid}/scenarios", json={"domain_model_id": dm, "name": name, "content": content}
    )["id"]


def scen(
    discipline: str,
    engine: str,
    controls: dict,
    *,
    seed: int | None,
    description: str,
    baseline: bool = False,
    assumptions: list[dict] | None = None,
    timeout: int = 900,
) -> dict:
    return {
        "discipline": discipline,
        "engine_id": engine,
        "is_baseline": baseline,
        "description": description,
        "controls": controls,
        "settings": {"seed": seed, "timeout_s": timeout},
        "assumptions": assumptions or [],
    }


def run(api: Api, pid: str, rev_id: str, name: str) -> dict:
    r = api.post(
        f"/projects/{pid}/runs",
        headers={"Idempotency-Key": f"demo-{rev_id}"},
        json={"scenario_revision_id": rev_id, "label": _saved_label(name)},
    )
    if r["state"] != "completed":
        raise DemoError(f"run {name} ended {r['state']}: {r.get('error_message')}")
    return r


def metric(api: Api, run_id: str, metric_id: str, entity: str | None = None) -> dict:
    return next(
        m for m in api.get(f"/runs/{run_id}/metrics") if m["metric_id"] == metric_id and m["entity"] == entity
    )


def fmt(m: dict) -> str:
    return (
        f"{m['value']:.4g} {m['unit']}"
        if m["status"] == "available" and m["value"] is not None
        else m["status"]
    )


def compare_and_report(api: Api, pid: str, title: str, base: dict, others: list[dict], narrative: str) -> str:
    comp = api.post(
        f"/projects/{pid}/comparisons",
        json={"name": title, "baseline_run_id": base["id"], "run_ids": [o["id"] for o in others]},
    )
    rep = api.post(
        f"/projects/{pid}/reports",
        json={
            "kind": "comparison",
            "title": title,
            "comparison_id": comp["id"],
            "narrative": narrative,
            "narrative_origin": "manual",
        },
    )
    if rep["status"] != "completed":
        raise DemoError(f"report {title} {rep['status']}: {rep.get('error')}")
    return rep["id"]


SOURCE_INFO = """# Source information

All material in this demonstration project is **synthetic**. It was created for Parallel from documented
reference models and does not describe a real site, organisation or incident.

{body}

Results shown in the project come from real engine runs recorded on this installation when the demonstration
was seeded. They are labelled "Saved result" with the recording date. Re-running a scenario repeats the
computation; nothing in the project imitates computation.
"""


# ---------------------------------------------------------------- 1. mine evacuation
def build_mine(api: Api, ctx: dict) -> str:
    from parallel_engines import reference_models as rm

    pid = new_project(
        api,
        ctx,
        "mine_evacuation",
        name="Level 3 mine section: evacuation",
        project_type="mine_evacuation",
        description="Demonstration: evacuation of a level mine section with a blocked route and a "
        "proposed escapeway (synthetic).",
        location_label="Synthetic level mine section",
    )
    m = rm.mine_section_model().content
    objects = [obj("walkable", "Walkable drift floor", "walkable_area", m.walkable_area.model_dump())]
    objects += [obj(o.id, o.name, "obstacle", poly(o.polygon)) for o in m.obstacles]
    objects += [
        obj(e.id, e.name, "refuge" if e.kind == "refuge" else "exit", poly(e.polygon)) for e in m.exits
    ]
    objects += [
        obj(g.id, g.name, "start_zone", poly(g.zone), population=(g.count, "count")) for g in m.start_groups
    ]
    objects += [obj(r.id, r.name, "route_segment", poly(r.polygon)) for r in m.route_segments]
    scene_rev = plan_scene(
        api,
        pid,
        "Level 3 section plan (synthetic)",
        objects,
        [
            assumption(
                "level",
                "The section is approximately level.",
                "The first verified movement model covers one "
                "level section; multi-level transfers are out of scope.",
            ),
            assumption("plan", "Plan geometry is synthetic.", "No survey of a real mine was used."),
        ],
        "Prepared plan geometry: walkable floor, portals, refuge, crew start zones and route segments",
    )
    dm = api.post(
        f"/scene-revisions/{scene_rev}/derive-model",
        json={"discipline": "pedestrian", "name": "Level 3 movement model"},
    )["domain_model_id"]
    # layout alternative: a proposed escapeway from the face area to the refuge chamber, as its own model
    escape = [
        obj(
            "escapeway_north",
            "Proposed escapeway (north leg)",
            "walkable_area",
            {"type": "Polygon", "coordinates": [[[28, 4], [31, 4], [31, 24], [28, 24], [28, 4]]]},
            layer="proposal",
        ),
        obj(
            "escapeway_east",
            "Proposed escapeway (east leg)",
            "walkable_area",
            {"type": "Polygon", "coordinates": [[[28, 21], [35, 21], [35, 24], [28, 24], [28, 21]]]},
            layer="proposal",
        ),
    ]
    for o in escape:
        o["belongs_to"] = "hypothetical_scenario"
    scene_id = api.get(f"/scene-revisions/{scene_rev}")["scene_id"]
    content = api.get(f"/scene-revisions/{scene_rev}")["content"]
    content["spatial_objects"] += escape
    rev2 = api.post(
        f"/scenes/{scene_id}/revisions",
        json={"content": content, "message": "Proposed escapeway (hypothetical)"},
    )
    rev2 = api.post(f"/scene-revisions/{rev2['id']}/publish")["id"]
    base_model = api.get(f"/model-revisions/{api.get(f'/models/{dm}')['current_revision_id']}")["content"]
    import shapely

    from parallel_contracts.geometry import PolygonGeom
    from parallel_scene.geometry import to_shapely

    walk = shapely.union_all(
        [
            to_shapely(PolygonGeom.model_validate(g))
            for g in [base_model["content"]["walkable_area"], *[o["geometry"] for o in escape]]
        ]
    )
    proposed = {
        **base_model,
        "name": "Level 3 movement model (proposed escapeway)",
        "scene_revision_id": rev2,
        "content": {
            **base_model["content"],
            "walkable_area": {"type": "Polygon", "coordinates": [[list(c) for c in walk.exterior.coords]]},
        },
        "provenance": {
            "kind": "derived",
            "derived_from": [dm, rev2],
            "note": "Layout alternative: the prepared model with the proposed escapeway added",
        },
    }
    dm_alt = api.post(f"/projects/{pid}/models", json={"name": proposed["name"], "content": proposed})[
        "domain_model_id"
    ]
    pre = rm.premovement(5, 15).model_dump(mode="json")
    common = [
        assumption(
            "premove",
            "Pre-movement time is uniform between 5 and 15 s.",
            "Assumed range for a trained crew after an alarm; replace with site drill data.",
            ["controls.pre_movement"],
        )
    ]
    revs = {
        "baseline": scenario(
            api,
            pid,
            dm,
            "Baseline",
            scen(
                "pedestrian",
                "jupedsim",
                {"kind": "pedestrian", "pre_movement": pre},
                seed=42,
                baseline=True,
                assumptions=common,
                description="All routes and exits available; crews use the nearest reachable exit.",
            ),
        ),
        "blocked": scenario(
            api,
            pid,
            dm,
            "West drift blocked",
            scen(
                "pedestrian",
                "jupedsim",
                {"kind": "pedestrian", "pre_movement": pre, "closed_route_segments": ["west_drift"]},
                seed=42,
                assumptions=common,
                description="The west drift (x 18-20 m) is unavailable from the start.",
            ),
        ),
        "proposed": scenario(
            api,
            pid,
            dm_alt,
            "Proposed: new escapeway",
            scen(
                "pedestrian",
                "jupedsim",
                {"kind": "pedestrian", "pre_movement": pre, "closed_route_segments": ["west_drift"]},
                seed=42,
                assumptions=common,
                description="West drift blocked; a proposed escapeway (x 28-31 m) leads from the "
                "face area to the refuge chamber, separating the face crew from the crosscut.",
            ),
        ),
    }
    runs = {k: run(api, pid, v, k) for k, v in revs.items()}
    t = {k: metric(api, r["id"], "evac.t100") for k, r in runs.items()}
    narrative = (
        "All three runs use the same initial population and random seed (42). The proposed escapeway is a layout "
        "alternative and runs on its own model revision, which the comparison flags. Total clearance time "
        "(evac.t100): baseline {b} (run {rb}); west drift blocked {x} (run {rx}); blocked with the proposed "
        "escapeway {p} (run {rp}). Results are reported as computed."
    ).format(
        b=fmt(t["baseline"]),
        x=fmt(t["blocked"]),
        p=fmt(t["proposed"]),
        rb=runs["baseline"]["id"],
        rx=runs["blocked"]["id"],
        rp=runs["proposed"]["id"],
    )
    compare_and_report(
        api,
        pid,
        "Blocked west drift and proposed escapeway",
        runs["baseline"],
        [runs["blocked"], runs["proposed"]],
        narrative,
    )
    upload_text(
        api,
        pid,
        "source-information.md",
        SOURCE_INFO.format(
            body=(
                "* Geometry: Parallel reference model *Level mine section (synthetic)*, entered as a prepared plan scene.\n"
                "* Crews: 30 face crew, 20 workshop crew and 10 development crew (synthetic counts).\n"
                "* Movement: JuPedSim collision-free speed model defaults; pre-movement 5-15 s (assumption)."
            )
        ),
    )
    upload_text(api, pid, "presenter-notes.md", MINE_NOTES)
    return pid


MINE_NOTES = """# Presenter notes: mine evacuation

1. **Scene**: open *Level 3 section plan*. Point out the walkable drift, portals, refuge and the three crew start
   zones. These are prepared-model objects on a synthetic plan, not a capture.
2. **Prepare**: the movement model was derived from the scene revision (see the model's scene link).
3. **Scenarios**: baseline, *West drift blocked* (route unavailable from the start) and *Proposed: new
   escapeway*, a layout alternative on its own model revision (the comparison flags the different revision).
   All share seed 42 and the same population. Scene revision 2 shows the escapeway as a hypothetical object.
4. **Results**: evacuated-over-time curves, T50/T95/T100, unreachable people and exit usage. Emphasise the
   accounting identity: initial population = evacuated + unreachable + remaining.
5. **Honesty**: if anyone remains at the horizon, T100 shows as unattained; it is never replaced by the last
   exit time. The proposal is shown as computed; it is not required to win. (Simply directing the face crew to
   the refuge changes nothing here: the refuge is already its nearest exit once the west drift is closed.)
6. **Report**: the comparison report lists revisions, engine versions, seeds and input hashes for every run.
"""


# ---------------------------------------------------------------- 2. construction site
def build_construction(api: Api, ctx: dict) -> str:
    from parallel_engines import reference_models as rm

    pid = new_project(
        api,
        ctx,
        "construction",
        name="Riverside block: site logistics",
        project_type="construction",
        description="Demonstration: delivery operations with a second loading bay, and a site "
        "evacuation drill (synthetic).",
        location_label="Synthetic construction site",
    )

    def rect(x0, y0, x1, y1):
        return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    objects = [
        obj("yard", "Site yard", "walkable_area", rect(0, 0, 60, 40)),
        obj("building", "Building under construction", "obstacle", rect(15, 12, 45, 30)),
        obj("crane_base", "Tower crane base", "obstacle", rect(47, 20, 51, 24)),
        obj("material_stack", "Material stack", "obstacle", rect(5, 25, 10, 35)),
        obj("main_gate", "Main gate", "exit", rect(0, 18, 1, 22)),
        obj("emergency_gate", "Emergency gate", "exit", rect(59, 35, 60, 39)),
        obj(
            "scaffold_north",
            "North scaffold crew",
            "start_zone",
            rect(15, 31, 45, 34),
            population=(25, "count"),
        ),
        obj("laydown", "Laydown yard crew", "start_zone", rect(48, 5, 58, 15), population=(10, "count")),
        obj("groundworks", "Groundworks crew", "start_zone", rect(2, 2, 12, 10), population=(15, "count")),
        obj("bay", "Loading bay", "loading_bay", rect(30, 2, 40, 8), layer="logistics"),
        obj("gate_check", "Gate check point", "gate", rect(1, 16, 4, 24), layer="logistics"),
    ]
    scene_rev = plan_scene(
        api,
        pid,
        "Site logistics and evacuation plan (synthetic)",
        objects,
        [
            assumption(
                "layout", "The site layout is synthetic.", "Demonstration plan; no real site was surveyed."
            )
        ],
        "Prepared plan: yard, building footprint, gates, crew zones and logistics points",
    )
    evac_dm = api.post(
        f"/scene-revisions/{scene_rev}/derive-model",
        json={"discipline": "pedestrian", "name": "Site evacuation model"},
    )["domain_model_id"]
    ops = api.post(
        f"/projects/{pid}/models",
        json={"name": "Delivery operations", "content": rm.site_operations_model().model_dump(mode="json")},
    )
    ops_dm = ops["domain_model_id"]
    reps = [
        assumption(
            "reps",
            "Ten replications per scenario with common random numbers.",
            "Replication spread is reported; arrivals and durations are triangular assumptions.",
        )
    ]
    base = scenario(
        api,
        pid,
        ops_dm,
        "Existing layout deliveries",
        scen(
            "operations",
            "simpy",
            {"kind": "operations", "replications": 10},
            seed=7,
            baseline=True,
            assumptions=reps,
            description="One gate, one loading bay and one crane serve materials deliveries and concrete pours.",
        ),
    )
    alt = scenario(
        api,
        pid,
        ops_dm,
        "Second loading bay for concrete pours",
        scen(
            "operations",
            "simpy",
            {
                "kind": "operations",
                "replications": 10,
                "added_resources": [
                    {
                        "id": "bay2",
                        "name": "Second loading bay",
                        "kind": "loading_bay",
                        "capacity": 1,
                        "provenance": {"kind": "assumption", "note": "Proposed temporary bay"},
                    }
                ],
                "step_resource_changes": {"concrete.pour": "bay2"},
            },
            seed=7,
            assumptions=reps,
            description="Concrete pours move to a proposed second bay; materials deliveries keep the existing bay.",
        ),
    )
    drill = scenario(
        api,
        pid,
        evac_dm,
        "Site evacuation drill",
        scen(
            "pedestrian",
            "jupedsim",
            {"kind": "pedestrian", "pre_movement": rm.premovement(10, 30).model_dump(mode="json")},
            seed=11,
            baseline=True,
            description="All crews evacuate to the main or emergency gate after the site alarm.",
            assumptions=[
                assumption("premove", "Pre-movement 10-30 s.", "Assumed for crews stopping work at height.")
            ],
        ),
    )
    r_base, r_alt, r_drill = (
        run(api, pid, base, "existing layout"),
        run(api, pid, alt, "second bay"),
        run(api, pid, drill, "evacuation drill"),
    )
    w0, w1 = (
        metric(api, r_base["id"], "ops.mean_wait", "bay"),
        metric(api, r_alt["id"], "ops.mean_wait", "bay"),
    )
    compare_and_report(
        api,
        pid,
        "Second loading bay",
        r_base,
        [r_alt],
        (
            f"Mean wait at the existing loading bay (ops.mean_wait[bay]) is {fmt(w0)} in the existing layout "
            f"(run {r_base['id']}) and {fmt(w1)} with concrete pours moved to a second bay (run {r_alt['id']}). Both use "
            "seed 7 and ten replications, so arrivals and durations are matched across scenarios."
        ),
    )
    t = metric(api, r_drill["id"], "evac.t100")
    api.post(
        f"/projects/{pid}/reports",
        json={
            "kind": "project",
            "title": "Site evacuation drill",
            "run_ids": [r_drill["id"]],
            "narrative_origin": "manual",
            "narrative": f"Total clearance time (evac.t100) for the synthetic site drill is {fmt(t)} (run {r_drill['id']}).",
        },
    )
    upload_text(
        api,
        pid,
        "source-information.md",
        SOURCE_INFO.format(
            body=(
                "* Operations: Parallel reference model *Site operations (synthetic)*: triangular service times, "
                "exponential arrivals.\n* Evacuation: synthetic site plan with 50 workers in three zones."
            )
        ),
    )
    upload_text(
        api,
        pid,
        "presenter-notes.md",
        """# Presenter notes: construction site

1. **Scene**: the plan shows the yard, building footprint, gates, crew zones and the logistics points.
2. **Operational alternative**: compare *Existing layout deliveries* with *Second loading bay for concrete pours*.
   Both runs use ten replications with common random numbers; show the replicate statistics.
3. **Conservation**: SimPy results are cross-checked: every request is granted, waiting or unfinished.
4. **Evacuation**: the drill scenario uses the movement model derived from the same plan scene.
5. **Report**: two reports, one for the operational comparison and one for the drill.
""",
    )
    return pid


# ---------------------------------------------------------------- 3. urban planning
def build_urban_planning(api: Api, ctx: dict) -> str:
    from parallel_engines import reference_models as rm

    pid = new_project(
        api,
        ctx,
        "urban_planning",
        name="Crossroads corridor: junction control",
        project_type="urban_planning",
        description="Demonstration: a priority junction compared with a signalised junction (synthetic).",
        location_label="Synthetic four-arm crossroads",
    )
    env = rm.crossroads_model()
    nodes = {n.id: (n.x, n.y) for n in env.content.nodes}
    objects = [
        obj(
            e.id,
            f"Road {e.from_node} to {e.to_node}",
            "road",
            {"type": "LineString", "coordinates": [list(nodes[e.from_node]), list(nodes[e.to_node])]},
            layer="network",
        )
        for e in env.content.edges
    ]
    objects += [
        obj(
            f"node_{k}",
            f"Node {k}",
            "junction" if k == "C" else "node",
            {"type": "Point", "coordinates": list(v)},
            layer="network",
        )
        for k, v in nodes.items()
    ]
    scene_rev = plan_scene(
        api,
        pid,
        "Corridor plan (synthetic)",
        objects,
        [assumption("demand", "Traffic demand is synthetic.", "Demand is never inferred from road width.")],
        "Road centrelines and junctions of the synthetic corridor",
    )
    dm = api.post(
        f"/projects/{pid}/models",
        json={"name": "Crossroads network", "content": {**env.model_dump(mode="json"), "scene_revision_id": scene_rev}},
    )["domain_model_id"]
    a = [
        assumption(
            "seed", "Same demand and seed in both scenarios.", "Controlled comparison of junction control."
        )
    ]
    base = scenario(
        api,
        pid,
        dm,
        "Baseline: priority junction",
        scen(
            "traffic",
            "sumo",
            {"kind": "traffic"},
            seed=3,
            baseline=True,
            assumptions=a,
            description="Existing priority-controlled crossroads.",
        ),
    )
    prop = scenario(
        api,
        pid,
        dm,
        "Proposed: signalised junction",
        scen(
            "traffic",
            "sumo",
            {"kind": "traffic", "node_type_changes": {"C": "traffic_light"}},
            seed=3,
            assumptions=a,
            description="The central junction becomes signal controlled (SUMO default program).",
        ),
    )
    rb, rp = run(api, pid, base, "priority junction"), run(api, pid, prop, "signalised junction")
    mb, mp = (
        metric(api, rb["id"], "traffic.mean_travel_time"),
        metric(api, rp["id"], "traffic.mean_travel_time"),
    )
    cb, cp = (
        metric(api, rb["id"], "traffic.completion_ratio"),
        metric(api, rp["id"], "traffic.completion_ratio"),
    )
    compare_and_report(
        api,
        pid,
        "Junction control alternatives",
        rb,
        [rp],
        (
            f"Mean travel time (traffic.mean_travel_time) is {fmt(mb)} for the priority junction (run {rb['id']}) and "
            f"{fmt(mp)} with signals (run {rp['id']}); completion ratios (traffic.completion_ratio) are {fmt(cb)} and "
            f"{fmt(cp)}. Travel times are compared only alongside completion, so unfinished trips cannot masquerade as "
            "an improvement."
        ),
    )
    upload_text(
        api,
        pid,
        "source-information.md",
        SOURCE_INFO.format(
            body=(
                "* Network: Parallel reference model *Crossroads (synthetic)* with a north-east bypass.\n"
                "* Demand: synthetic origin-destination flows; never inferred from road width."
            )
        ),
    )
    upload_text(
        api,
        pid,
        "presenter-notes.md",
        """# Presenter notes: urban planning

1. **Scene**: corridor plan with road centrelines and the central junction.
2. **Scenarios**: priority junction (baseline) and signalised junction (proposed), same demand and seed.
3. **Results**: mean travel time and delay are always read alongside the completion ratio; SUMO's own trip
   statistics are cross-checked against Parallel's normalised values.
4. **Playback**: vehicle positions over time come from SUMO's floating-car output.
""",
    )
    return pid


# ---------------------------------------------------------------- 4. urban drainage
def build_drainage(api: Api, ctx: dict) -> str:
    from parallel_engines import reference_models as rm

    pid = new_project(
        api,
        ctx,
        "urban_drainage",
        name="Mill Lane catchment: flooding",
        project_type="urban_drainage",
        description="Demonstration: existing network, a silted conduit and a storage intervention "
        "under the same design storm (synthetic).",
        location_label="Synthetic urban catchment",
    )
    env = rm.drainage_model()
    c = env.content
    pts = {j.id: j.position for j in c.junctions} | {o.id: o.position for o in c.outfalls}
    objects = [
        obj(
            j.id,
            f"Junction {j.id}",
            "junction",
            {"type": "Point", "coordinates": list(j.position)},
            layer="network",
        )
        for j in c.junctions
    ]
    objects += [
        obj(
            o.id,
            f"Outfall {o.id}",
            "outfall",
            {"type": "Point", "coordinates": list(o.position)},
            layer="network",
        )
        for o in c.outfalls
    ]
    objects += [
        obj(
            k.id,
            f"Conduit {k.id}",
            "conduit",
            {"type": "LineString", "coordinates": [list(pts[k.from_node]), list(pts[k.to_node])]},
            layer="network",
        )
        for k in c.conduits
        if k.from_node in pts and k.to_node in pts
    ]
    scene_rev = plan_scene(
        api,
        pid,
        "Catchment network plan (synthetic)",
        objects,
        [
            assumption(
                "hidden",
                "Pipe sizes and inverts are synthetic inputs.",
                "Hidden drainage geometry is never inferred from surface imagery.",
            )
        ],
        "Junctions, outfalls and conduits of the synthetic network",
    )
    dm = api.post(
        f"/projects/{pid}/models",
        json={
            "name": "Mill Lane drainage network",
            "content": {**env.model_dump(mode="json"), "scene_revision_id": scene_rev},
        },
    )["domain_model_id"]
    a = [
        assumption(
            "storm",
            "The same 60-minute design storm in every scenario.",
            "Synthetic design storm (90 mm/h peak); rainfall hash recorded per run.",
        )
    ]
    revs = {
        "existing": scenario(
            api,
            pid,
            dm,
            "Existing network",
            scen(
                "drainage",
                "swmm",
                {"kind": "drainage"},
                seed=None,
                baseline=True,
                assumptions=a,
                description="Existing network under the design storm.",
            ),
        ),
        "constrained": scenario(
            api,
            pid,
            dm,
            "Constrained: conduit C3 half silted",
            scen(
                "drainage",
                "swmm",
                {"kind": "drainage", "blockages": [{"conduit_id": "C3", "sediment_depth_fraction": 0.5}]},
                seed=None,
                assumptions=a,
                description="Sediment fills half the depth of conduit C3.",
            ),
        ),
        "intervention": scenario(
            api,
            pid,
            dm,
            "Intervention: storage at J2",
            scen(
                "drainage",
                "swmm",
                {
                    "kind": "drainage",
                    "storage_conversions": [{"junction_id": "J2", "area_m2": 600, "max_depth_m": 2.2}],
                },
                seed=None,
                assumptions=a,
                description="Junction J2 becomes a 600 m² storage unit, 2.2 m deep.",
            ),
        ),
    }
    runs = {k: run(api, pid, v, k) for k, v in revs.items()}
    f = {k: metric(api, r["id"], "drainage.total_flooding_volume") for k, r in runs.items()}
    compare_and_report(
        api,
        pid,
        "Silted conduit and storage intervention",
        runs["existing"],
        [runs["constrained"], runs["intervention"]],
        (
            f"Total node flooding volume (drainage.total_flooding_volume): existing {fmt(f['existing'])} (run "
            f"{runs['existing']['id']}), C3 half silted {fmt(f['constrained'])} (run {runs['constrained']['id']}), storage "
            f"at J2 {fmt(f['intervention'])} (run {runs['intervention']['id']}). SWMM node flooding is a network result; "
            "it does not define a surface inundation extent."
        ),
    )
    upload_text(
        api,
        pid,
        "source-information.md",
        SOURCE_INFO.format(
            body=(
                "* Network: Parallel reference model *Urban drainage (synthetic)*: junctions, conduits, outfall and "
                "subcatchments.\n* Rainfall: synthetic 60-minute design storm, 90 mm/h peak."
            )
        ),
    )
    upload_text(
        api,
        pid,
        "presenter-notes.md",
        """# Presenter notes: urban drainage

1. **Scene**: the network plan shows junctions, conduits and the outfall.
2. **Scenarios**: existing network, a silted conduit (constrained condition) and storage at J2 (intervention),
   all under the same design storm; each run records the rainfall hash.
3. **Results**: flooding volume and duration per node, conduit surcharge; volumes are cross-checked against the
   SWMM report continuity tables.
4. **Limits**: node flooding is not a surface flood map; a 2D model (HEC-RAS exchange) is needed for that.
""",
    )
    return pid


# ---------------------------------------------------------------- 5. CrashLens
def build_crashlens(api: Api, ctx: dict) -> str:
    from parallel_core.db import session_scope
    from parallel_core.models import CaptureBatch
    from parallel_reconstruction.synthetic import generate, road_scene

    pid = new_project(
        api,
        ctx,
        "crashlens",
        name="Training junction collision",
        project_type="crashlens",
        description="Demonstration: a controlled training scene with known reference positions, "
        "evidence annotations and two hypotheses (synthetic).",
        location_label="Synthetic training road scene",
    )
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        gt = generate(road_scene(), out, n_frames=36, width=560, height_px=420, video=False, blur_every=9)
        batch = api.post(
            f"/projects/{pid}/capture-batches",
            json={"name": "Synthetic training scene photographs", "kind": "photos"},
        )
        with session_scope() as db:
            db.get(CaptureBatch, batch["id"]).synthetic = True  # type: ignore[union-attr]
        frames = {}
        for name in gt.frame_names:
            frames[name] = (out / "frames" / name).read_bytes()
            api.post(
                f"/projects/{pid}/assets",
                data={"capture_batch_id": batch["id"]},
                files={"file": (name, frames[name], "image/png")},
            )

        def table(rows, header):
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(header)
            w.writerows(rows)
            return buf.getvalue().encode()

        cams = table(
            [[n, *c] for n, c in zip(gt.frame_names, gt.camera_centres, strict=True)],
            ["frame", "x", "y", "z"],
        )
        refs = table([[k, *v] for k, v in gt.targets.items()], ["name", "x", "y", "z"])
        for fname, data in (("camera_positions.csv", cams), ("reference_points.csv", refs)):
            api.post(
                f"/projects/{pid}/assets",
                data={"capture_batch_id": batch["id"]},
                files={"file": (fname, data, "text/csv")},
            )
    job = api.post(
        f"/projects/{pid}/reconstructions",
        json={
            "capture_batch_id": batch["id"],
            "settings": {
                "scene_name": "Training road scene",
                "reference_source": "synthetic surveyed camera positions",
                "tolerance_abs_m": 0.10,
            },
        },
    )
    if job["status"] != "completed":
        raise DemoError(f"reconstruction {job['status']}: {job.get('error')}")
    case = api.post(
        f"/projects/{pid}/cases",
        json={
            "reference": "TC-DEMO-01",
            "title": "Training junction collision",
            "training": True,
            "location_note": "Synthetic training scene",
        },
    )
    cid = case["id"]
    api.post(f"/cases/{cid}/assignments", json={"user_id": ctx["reviewer_id"], "role": "reviewer"})
    photos = []
    for i, name in enumerate(list(frames)[:3], 1):
        photos.append(
            api.post(
                f"/cases/{cid}/evidence",
                data={
                    "kind": "image",
                    "title": f"Scene photograph {i} (synthetic training capture)",
                    "captured_by": "Training",
                    "source_metadata": json.dumps({"synthetic": True, "frame": name}),
                },
                files={"file": (name, frames[name], "image/png")},
            )["id"]
        )
    prov = {"kind": "synthetic", "note": "Training vehicle data"}
    v1 = api.post(
        f"/cases/{cid}/vehicles",
        json={
            "label": "Vehicle 1 (red hatchback)",
            "mass_kg": 1350,
            "length_m": 4.4,
            "width_m": 1.8,
            "data_source": "Training data sheet",
            "provenance": prov,
        },
    )["id"]
    v2 = api.post(
        f"/cases/{cid}/vehicles",
        json={
            "label": "Vehicle 2 (blue saloon)",
            "mass_kg": 1600,
            "length_m": 4.7,
            "width_m": 1.9,
            "data_source": "Training data sheet",
            "provenance": prov,
        },
    )["id"]

    def observe(**body):
        return api.post(f"/cases/{cid}/observations", json={"statement_type": "observation", **body})["id"]

    skid = observe(
        kind="skid_mark",
        text="Dark tyre mark in the eastbound lane, ending at Vehicle 1's front axle",
        evidence_item_id=photos[0],
        geometry={"type": "LineString", "coordinates": [[-10.4, 1.0], [0.3, 1.2]]},
        measurement_value=10.7,
        measurement_unit="m",
        uncertainty={"kind": "absolute", "value": 0.3, "unit": "m", "note": "two tape readings"},
        limitation="The start of the mark is faint in photograph 1.",
    )
    rest1 = observe(
        kind="final_position",
        text="Vehicle 1 (red hatchback) at rest facing east",
        evidence_item_id=photos[0],
        position=[2.5, 1.2],
        uncertainty={"kind": "absolute", "value": 0.25, "unit": "m"},
    )
    rest2 = observe(
        kind="final_position",
        text="Vehicle 2 (blue saloon) at rest across the westbound lane",
        evidence_item_id=photos[1],
        position=[-2.2, -1.6],
        uncertainty={"kind": "absolute", "value": 0.25, "unit": "m"},
    )
    debris = observe(
        kind="debris",
        text="Glass and plastic debris field between the vehicles",
        evidence_item_id=photos[2],
        position=[0.4, -0.3],
        uncertainty={"kind": "absolute", "value": 1.0, "unit": "m"},
    )
    observe(
        kind="road_condition",
        text="Dry asphalt; lane markings intact",
        evidence_item_id=photos[2],
        position=[0, 0],
    )
    api.post(
        f"/cases/{cid}/calculations/skid-speed",
        json={
            "observation_id": skid,
            "friction_low": 0.65,
            "friction_high": 0.8,
            "friction_basis": "dry asphalt range, training assumption",
        },
    )
    h1 = api.post(
        f"/cases/{cid}/hypotheses",
        json={
            "label": "H1: Vehicle 1 braked before impact",
            "content": {
                "description": "Vehicle 1 braked hard in the eastbound lane before striking Vehicle 2 turning across it.",
                "participant_ids": [v1, v2],
                "sequence": [
                    {
                        "order": 1,
                        "description": "Vehicle 2 begins a turn across the eastbound lane",
                        "participant_ids": [v2],
                    },
                    {
                        "order": 2,
                        "description": "Vehicle 1 brakes, leaving the tyre mark",
                        "participant_ids": [v1],
                    },
                    {"order": 3, "description": "Impact and rest", "participant_ids": [v1, v2]},
                ],
                "unresolved_questions": [
                    "Signal phase at the time of impact",
                    "Speed at the start of braking",
                ],
                "analyst_interpretation": "Consistent with the tyre mark ending at Vehicle 1's front axle.",
            },
        },
    )["id"]
    h2 = api.post(
        f"/cases/{cid}/hypotheses",
        json={
            "label": "H2: No braking before impact",
            "content": {
                "description": "Vehicle 1 did not brake before impact; the tyre mark predates the incident.",
                "participant_ids": [v1, v2],
                "unresolved_questions": ["Age of the tyre mark"],
                "analyst_interpretation": "Requires evidence that the mark predates the incident.",
            },
        },
    )["id"]
    for hid, oid, rel, why in (
        (h1, skid, "supports", "The mark ends at Vehicle 1's front axle."),
        (h1, rest1, "supports", "Rest position aligned with the mark."),
        (h2, skid, "contradicts", "A fresh mark ending at the vehicle argues against no braking."),
        (h1, debris, "context", "Debris locates the impact area."),
        (h2, debris, "context", "Debris locates the impact area."),
    ):
        api.post(f"/hypotheses/{hid}/links", json={"observation_id": oid, "relation": rel, "rationale": why})
    for t, desc, st, sid, hid in (
        (0.0, "Photographs taken after the vehicles came to rest", "evidence", photos[0], None),
        (-2.5, "Vehicle 1 begins braking (proposed)", "hypothesis", h1, h1),
        (0.0, "Impact (proposed)", "hypothesis", h1, h1),
    ):
        api.post(
            f"/cases/{cid}/timeline",
            json={
                "t_offset_s": t,
                "t_uncertainty_s": 1.0,
                "description": desc,
                "source_type": st,
                "source_id": sid,
                "hypothesis_id": hid,
            },
        )
    api.post(
        f"/cases/{cid}/collision-model",
        json={
            "name": "TC-DEMO-01 collision model",
            "scene_revision_id": job["result_scene_revision_id"],
            "friction_low": 0.65,
            "friction_high": 0.8,
            "friction_basis": "dry asphalt range, training assumption",
            "vehicle_record_ids": [v1, v2],
            "targets": [
                {"observation_id": rest1, "kind": "rest_position", "vehicle_record_id": v1, "heading_deg": 0},
                {
                    "observation_id": rest2,
                    "kind": "rest_position",
                    "vehicle_record_id": v2,
                    "heading_deg": None,
                },
                {
                    "observation_id": debris,
                    "kind": "debris_centroid",
                    "vehicle_record_id": None,
                    "heading_deg": None,
                },
            ],
        },
    )
    rep = api.post(
        f"/projects/{pid}/reports",
        json={
            "kind": "case",
            "title": "TC-DEMO-01 case report",
            "case_id": cid,
            "narrative_origin": "manual",
            "narrative": (
                "This training case documents a synthetic scene reconstructed from 36 photographs and two alternative "
                "hypotheses. No collision engine result is included: a licensed Virtual CRASH or PC-Crash installation is "
                "required to test the hypotheses against the measured evidence targets."
            ),
        },
    )
    if rep["status"] != "completed":
        raise DemoError(f"case report {rep['status']}: {rep.get('error')}")
    upload_text(
        api,
        pid,
        "source-information.md",
        SOURCE_INFO.format(
            body=(
                "* Scene: 36 synthetic photographs ray-traced by Parallel's training scene generator, with surveyed "
                "camera positions used as control points and held-out positions as independent checks.\n"
                "* Evidence, vehicles and hypotheses: synthetic training records.\n"
                "* No collision-engine result is included; no external result has been imitated."
            )
        ),
    )
    upload_text(
        api,
        pid,
        "presenter-notes.md",
        """# Presenter notes: CrashLens

1. **Scene reconstruction**: open the reconstruction job; show frame filtering (blurred frames rejected), COLMAP
   pose estimation and the scale check against held-out survey positions. Training is skipped honestly when no
   GPU worker is available; the untrained Gaussian initialisation is labelled as such.
2. **Evidence**: three photographs stored as originals with SHA-256 hashes; verify one live. Explain that a hash
   shows the file is unchanged, not that its content or timestamp is authentic.
3. **Observations vs interpretations**: the skid mark, rest positions and debris are observations; the skid-speed
   range is an interpretation that cites the skid observation and states its limits.
4. **Hypotheses**: H1 and H2 side by side with supporting and contradicting links. There is no probability of
   truth; evidence fit is "not evaluated" until a collision engine result is imported.
5. **Access**: only assigned investigators and reviewers see the case.
6. **Report**: the case report carries evidence identifiers, hashes and hypothesis identifiers, and states that
   it does not determine fault.
""",
    )
    return pid


BUILDERS = {
    "mine_evacuation": build_mine,
    "construction": build_construction,
    "urban_planning": build_urban_planning,
    "urban_drainage": build_drainage,
    "crashlens": build_crashlens,
}


# ---------------------------------------------------------------- reset
def reset() -> dict[str, int]:
    """Delete demonstration projects (demo_key set) and their stored files. Nothing else is touched."""
    from sqlalchemy import delete, select

    from parallel_core.db import session_scope
    from parallel_core.models import (
        AssistantSession,
        CaptureBatch,
        Case,
        Comparison,
        DomainModel,
        OptimisationStudy,
        Project,
        ProjectMembership,
        ReconstructionJob,
        Report,
        ReviewRecord,
        Scenario,
        Scene,
        SimulationRun,
        SourceAsset,
    )
    from parallel_core.storage import get_storage

    store = get_storage()
    counts = {"projects": 0, "files": 0}
    with session_scope() as db:
        projects = db.scalars(select(Project).where(Project.demo_key.is_not(None))).all()
        for p in projects:
            run_ids = list(db.scalars(select(SimulationRun.id).where(SimulationRun.project_id == p.id)))
            job_ids = list(
                db.scalars(select(ReconstructionJob.id).where(ReconstructionJob.project_id == p.id))
            )
            for model in (
                Report,
                Comparison,
                OptimisationStudy,
                SimulationRun,
                Scenario,
                DomainModel,
                Case,
                ReconstructionJob,
                Scene,
            ):
                db.execute(delete(model).where(model.project_id == p.id))
            # derivatives first: removing an original must not rewrite an evidence record (immutability trigger)
            db.execute(
                delete(SourceAsset).where(
                    SourceAsset.project_id == p.id, SourceAsset.derived_from_id.is_not(None)
                )
            )
            for model in (SourceAsset, CaptureBatch, AssistantSession, ReviewRecord, ProjectMembership):
                db.execute(delete(model).where(model.project_id == p.id))
            db.delete(p)
            db.flush()
            prefixes = [
                f"projects/{p.id}/",
                f"reports/{p.id}/",
                *[f"runs/{r}/" for r in run_ids],
                *[f"reconstructions/{j}/" for j in job_ids],
            ]
            counts["files"] += sum(store.delete_prefix(x) for x in prefixes)
            counts["projects"] += 1
    return counts


# ---------------------------------------------------------------- seed / status / CLI
def seed(only: list[str] | None = None) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from parallel_api.main import create_app
    from parallel_core.db import session_scope
    from parallel_core.models import Project

    ctx = ensure_org_and_users()
    if ctx["new_password"]:
        print(
            f"Created demo users {PRESENTER_EMAIL} and {REVIEWER_EMAIL} with password: {ctx['new_password']}"
        )
    results: dict[str, Any] = {}
    with TestClient(create_app()) as client:
        api = Api(client, ctx["token"])
        for key in DEMO_KEYS:
            if only and key not in only:
                continue
            with session_scope() as db:
                if db.scalar(select(Project.id).where(Project.demo_key == key)):
                    results[key] = "exists"
                    continue
            t0 = time.time()
            try:
                results[key] = {"project_id": BUILDERS[key](api, ctx), "seconds": round(time.time() - t0, 1)}
            except DemoError as e:
                results[key] = {"error": str(e)}
                log.error("demo %s failed: %s", key, e)
            print(f"{key}: {results[key]}")
    return results


def status() -> dict[str, Any]:
    from sqlalchemy import func, select

    from parallel_core.db import session_scope
    from parallel_core.models import Project, Report, SimulationRun

    with session_scope() as db:
        out = {}
        for p in db.scalars(select(Project).where(Project.demo_key.is_not(None))):
            out[p.demo_key] = {
                "project_id": p.id,
                "name": p.name,
                "completed_runs": db.scalar(
                    select(func.count())
                    .select_from(SimulationRun)
                    .where(SimulationRun.project_id == p.id, SimulationRun.state == "completed")
                ),
                "reports": db.scalar(
                    select(func.count()).select_from(Report).where(Report.project_id == p.id)
                ),
            }
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m parallel_api.demo", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--only", nargs="*", choices=DEMO_KEYS)
    r = sub.add_parser("reset")
    r.add_argument("--no-seed", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    os.environ.setdefault(
        "PARALLEL_TASK_ALWAYS_EAGER", "1"
    )  # runs execute here; must precede settings import
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args.cmd == "reset":
        print(json.dumps({"deleted": reset()}))
        if args.no_seed:
            return 0
    res = seed(getattr(args, "only", None))
    return 1 if any(isinstance(v, dict) and "error" in v for v in res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
