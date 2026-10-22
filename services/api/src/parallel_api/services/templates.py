"""Starter content for new projects created from a demonstration template.

Templates create clearly labelled synthetic domain models and scenarios (from
the engines' reference models) so users can start from a prepared example.
They never create results: results only come from runs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from parallel_contracts.scenario import ScenarioContent
from parallel_core.models import DomainModel, Project, Scenario
from parallel_core.revisions import create_model_revision, create_scenario_revision
from parallel_engines import reference_models as rm

from ..routers.models import validate_envelope

TEMPLATE_CONTENT = {
    "mine_evacuation": [(rm.mine_section_model, [("Baseline", rm.pedestrian_scenario(is_baseline=True))])],
    "urban_drainage": [(rm.drainage_model, [("Existing system", rm.drainage_scenario(is_baseline=True))])],
    "urban_planning": [(rm.crossroads_model, [("Baseline traffic", rm.traffic_scenario(is_baseline=True))])],
    "construction": [
        (rm.site_operations_model, [("Existing layout deliveries", rm.operations_scenario(is_baseline=True))])
    ],
    "crashlens": [],
}


def add_model(
    db: Session, project: Project, envelope, user_id: str | None, name: str | None = None
) -> DomainModel:
    dm = DomainModel(project_id=project.id, name=name or envelope.name, discipline=envelope.discipline.value)
    db.add(dm)
    db.flush()
    create_model_revision(db, dm, envelope, user_id, validate_envelope(envelope), message="from template")
    return dm


def add_scenario(
    db: Session, project: Project, dm: DomainModel, name: str, content: ScenarioContent, user_id: str | None
) -> Scenario:
    sc = Scenario(
        project_id=project.id,
        domain_model_id=dm.id,
        name=name,
        engine_id=content.engine_id,
        is_baseline=content.is_baseline,
    )
    db.add(sc)
    db.flush()
    create_scenario_revision(db, sc, content, user_id, origin="manual", message="from template")
    return sc


def apply_template(db: Session, project: Project, template: str, user_id: str) -> None:
    for model_fn, scenarios in TEMPLATE_CONTENT.get(template, []):
        dm = add_model(db, project, model_fn(), user_id)
        for name, content in scenarios:
            add_scenario(db, project, dm, name, content, user_id)
