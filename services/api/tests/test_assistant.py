"""The assistant: scoped tools under the user's permissions, adoption before use, grounded results,
honest unavailability, and the Anthropic request contract (checked against a stub client, no network)."""

from __future__ import annotations

import json
import os

import pytest

from parallel_api.assistant import providers
from parallel_api.assistant.providers import ProviderError, ScriptedProvider
from parallel_core.settings import get_settings


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setenv("PARALLEL_ASSISTANT_PROVIDER", "scripted")
    get_settings.cache_clear()
    yield lambda steps: providers.use_scripted(ScriptedProvider(steps))
    providers.use_scripted(None)
    monkeypatch.delenv("PARALLEL_ASSISTANT_PROVIDER")
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def mine(client, world):
    ed = world["editor"].h
    pid = client.post(
        "/api/v1/projects",
        headers=ed,
        json={
            "organisation_id": world["org_id"],
            "name": "Assistant mine",
            "project_type": "mine_evacuation",
            "template": "mine_evacuation",
        },
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/projects/{pid}/members",
            headers=world["admin"].h,
            json={"user_id": world["viewer"].id, "role": "viewer"},
        ).status_code
        == 201
    )
    return pid


def tool_results(messages):
    """Tool results in the last user turn, keyed by tool_use_id."""
    return {
        b["tool_use_id"]: json.loads(b["content"])
        for b in messages[-1]["content"]
        if b["type"] == "tool_result"
    }


def last_result(messages):
    return list(tool_results(messages).values())[-1]


def test_unavailable_without_provider_or_policy(client, world, mine):
    ed = world["editor"].h
    st = client.get(f"/api/v1/projects/{mine}/assistant/status", headers=ed).json()
    assert st["available"] is False and "credentials" in st["reason"]  # no key on this server
    assert client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=ed).status_code == 503
    # manual workflows are unaffected
    assert client.get(f"/api/v1/projects/{mine}/scenarios", headers=ed).status_code == 200


def test_draft_needs_adoption_then_runs_and_results_are_grounded(client, world, mine, scripted):
    ed = world["editor"].h
    state: dict = {}

    def draft(messages):
        res = tool_results(messages)
        model = next(
            v
            for v in res.values()
            if "domain_models" in v
            and isinstance(v["domain_models"], list)
            and v["domain_models"]
            and "objects" in v["domain_models"][0]
        )["domain_models"][0]
        segs = [x["id"] for x in model["objects"]["route_segments"]["items"]]
        assert "east_drift" in segs
        state["model"] = model["id"]
        content = {
            "discipline": "pedestrian",
            "engine_id": "jupedsim",
            "controls": {"kind": "pedestrian", "closed_route_segments": ["east_drift"]},
            "settings": {"seed": 42, "timeout_s": 600},
        }
        return {
            "tool_calls": [
                {
                    "name": "create_scenario_draft",
                    "input": {
                        "domain_model_id": model["id"],
                        "name": "East drift unavailable",
                        "scenario_json": json.dumps(content),
                        "explanation": "Closes the east drift route segment from the start.",
                    },
                }
            ]
        }

    def try_submit(messages):
        state["draft"] = last_result(messages)
        return {
            "tool_calls": [
                {
                    "name": "submit_run",
                    "input": {"scenario_revision_id": state["draft"]["scenario_revision_id"]},
                }
            ]
        }

    def explain(messages):
        state["submit"] = last_result(messages)
        return {"text": "I drafted the scenario; it needs your adoption before it can run."}

    scripted(
        [
            {
                "tool_calls": [
                    {"name": "inspect_project", "input": {}},
                    {"name": "list_domain_models", "input": {"discipline": "pedestrian"}},
                ]
            },
            draft,
            try_submit,
            explain,
        ]
    )
    st = client.get(f"/api/v1/projects/{mine}/assistant/status", headers=ed).json()
    assert st == {"available": True, "reason": None, "provider": "scripted", "model": "scripted-test-model"}
    sess = client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=ed).json()
    r = client.post(
        f"/api/v1/assistant/sessions/{sess['id']}/messages",
        headers=ed,
        json={"text": "Compare evacuation with the eastern route unavailable."},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    calls = [i for i in items if i["type"] == "tool_call"]
    assert [c["tool"] for c in calls] == [
        "inspect_project",
        "list_domain_models",
        "create_scenario_draft",
        "submit_run",
    ]
    assert all(c["authorised"] for c in calls)
    assert state["draft"]["adopted"] is False and state["draft"]["validation"]["ok"] is True
    assert "adopt" in state["submit"]["error"]  # unadopted drafts never run
    assert calls[2]["suggestion"] is True and calls[3]["error"]
    assert items[-1]["type"] == "assistant" and "adoption" in items[-1]["text"]
    rev = client.get(
        f"/api/v1/scenario-revisions/{state['draft']['scenario_revision_id']}", headers=ed
    ).json()
    assert rev["origin"] == "ai" and rev["adopted_by"] is None
    assert rev["content"]["provenance"]["kind"] == "ai_suggested"
    # the user adopts the draft explicitly
    a = client.post(f"/api/v1/assistant/tool-calls/{calls[2]['id']}/adopt", headers=ed)
    assert a.status_code == 200, a.text
    rev = client.get(
        f"/api/v1/scenario-revisions/{state['draft']['scenario_revision_id']}", headers=ed
    ).json()
    assert rev["adopted_by"] == world["editor"].id
    assert rev["content"]["provenance"]["adopted_by"] == world["editor"].id
    assert client.post(f"/api/v1/assistant/tool-calls/{calls[2]['id']}/adopt", headers=ed).status_code == 409

    # second turn: the adopted draft runs on the real engine and results are read back with identifiers
    def results(messages):
        state["run"] = last_result(messages)
        return {
            "tool_calls": [
                {
                    "name": "get_run_results",
                    "input": {"run_id": state["run"]["run_id"], "metric_prefix": "evac.t", "entity": None},
                }
            ]
        }

    def answer(messages):
        state["results"] = last_result(messages)
        t100 = next(m for m in state["results"]["metrics"] if m["metric_id"] == "evac.t100")
        return {"text": f"evac.t100 = {t100['value']} s on run {state['run']['run_id']}."}

    providers.use_scripted(
        ScriptedProvider(
            [
                {
                    "tool_calls": [
                        {
                            "name": "submit_run",
                            "input": {"scenario_revision_id": state["draft"]["scenario_revision_id"]},
                        }
                    ]
                },
                results,
                answer,
            ]
        )
    )
    r = client.post(f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=ed, json={"text": "Run it."})
    assert r.status_code == 200, r.text
    assert state["run"]["state"] == "completed"
    stored = {
        m["metric_id"]: m["value"]
        for m in client.get(f"/api/v1/runs/{state['run']['run_id']}/metrics", headers=ed).json()
    }
    assert {m["metric_id"]: m["value"] for m in state["results"]["metrics"]}["evac.t100"] == stored[
        "evac.t100"
    ]
    call = next(i for i in r.json()["items"] if i["type"] == "tool_call" and i["tool"] == "get_run_results")
    assert {"type": "metric", "id": state["run"]["run_id"], "metric_id": "evac.t100", "entity": None} in call[
        "source_refs"
    ]

    # report narrative suggestion -> adopted into a real report
    providers.use_scripted(
        ScriptedProvider(
            [
                {
                    "tool_calls": [
                        {
                            "name": "draft_report",
                            "input": {
                                "title": "East drift closure",
                                "run_ids": [state["run"]["run_id"]],
                                "narrative": f"With the east drift closed, evac.t100 on run {state['run']['run_id']} is recorded.",
                            },
                        }
                    ]
                },
                {"text": "I drafted a report narrative for you to review."},
            ]
        )
    )
    r = client.post(
        f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=ed, json={"text": "Draft a report."}
    )
    sug = next(i for i in r.json()["items"] if i["type"] == "tool_call" and i["tool"] == "draft_report")
    a = client.post(f"/api/v1/assistant/tool-calls/{sug['id']}/adopt", headers=ed).json()
    rep = client.get(f"/api/v1/reports/{a['report_id']}", headers=ed).json()
    assert rep["status"] == "completed" and rep["manifest"]["narrative"]["origin"] == "ai_draft_adopted"
    # sessions are private
    assert (
        client.get(f"/api/v1/assistant/sessions/{sess['id']}", headers=world["viewer"].h).status_code == 404
    )


def test_tools_enforce_the_users_permissions(client, world, mine, scripted):
    vw = world["viewer"].h
    models = client.get(f"/api/v1/projects/{mine}/models", headers=vw).json()
    before = len(client.get(f"/api/v1/projects/{mine}/scenarios", headers=vw).json())
    scripted(
        [
            {
                "tool_calls": [
                    {
                        "name": "create_scenario_draft",
                        "input": {
                            "domain_model_id": models[0]["id"],
                            "name": "x",
                            "explanation": "x",
                            "scenario_json": json.dumps(
                                {
                                    "discipline": "pedestrian",
                                    "engine_id": "jupedsim",
                                    "controls": {"kind": "pedestrian"},
                                }
                            ),
                        },
                    },
                    {"name": "list_case_evidence", "input": {"case_id": "nope", "vehicle_record_id": None}},
                    {"name": "get_run_status", "input": {"run_id": "not-a-run"}},
                ]
            },
            {"text": "You do not have permission to create scenarios here."},
        ]
    )
    sess = client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=vw).json()
    items = client.post(
        f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=vw, json={"text": "Draft a scenario"}
    ).json()["items"]
    calls = {i["tool"]: i for i in items if i["type"] == "tool_call"}
    assert (
        calls["create_scenario_draft"]["authorised"] is False
        and "Not permitted" in calls["create_scenario_draft"]["error"]
    )
    assert "policy" in calls["list_case_evidence"]["error"]
    assert "not found" in calls["get_run_status"]["error"]
    assert len(client.get(f"/api/v1/projects/{mine}/scenarios", headers=vw).json()) == before


def test_provider_errors_and_refusals_are_reported_not_hidden(client, world, mine, scripted):
    ed = world["editor"].h

    def boom(_):
        raise ProviderError("The language-model provider could not be reached.")

    scripted([boom])
    sess = client.post(f"/api/v1/projects/{mine}/assistant/sessions", headers=ed).json()
    items = client.post(
        f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=ed, json={"text": "hello"}
    ).json()["items"]
    assert items[-1] == {
        "seq": items[-1]["seq"],
        "type": "notice",
        "kind": "provider_error",
        "text": "The language-model provider could not be reached.",
    }
    providers.use_scripted(
        ScriptedProvider([{"stop_reason": "refusal", "refusal": {"category": "bio", "explanation": None}}])
    )
    items = client.post(
        f"/api/v1/assistant/sessions/{sess['id']}/messages", headers=ed, json={"text": "again"}
    ).json()["items"]
    assert items[-1]["kind"] == "refusal" and items[-1]["category"] == "bio"
    # the replayed history never contains notices, and user turns stay in order
    req = providers._scripted.requests[-1]  # type: ignore[union-attr]
    texts = [
        b["text"] for m in req["messages"] if m["role"] == "user" for b in m["content"] if b["type"] == "text"
    ]
    assert texts == ["hello", "again"]


def test_anthropic_request_contract(monkeypatch):
    """The Anthropic provider sends the documented request shape (stub client, no network)."""
    import anthropic
    from anthropic.types.beta import BetaMessage

    from parallel_api.assistant.providers import AnthropicProvider
    from parallel_api.assistant.service import SYSTEM_PROMPT
    from parallel_api.assistant.tools import definitions

    sent: dict = {}

    class StubMessages:
        def create(self, **kw):
            sent.update(kw)
            return BetaMessage.model_validate(
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [
                        {"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "tool_use", "id": "toolu_1", "name": "inspect_project", "input": {}},
                    ],
                    "stop_reason": "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            )

    class StubClient:
        def __init__(self, **_):
            self.beta = type("B", (), {"messages": StubMessages()})()

    monkeypatch.setattr(anthropic, "Anthropic", StubClient)
    p = AnthropicProvider(get_settings().model_copy(update={"anthropic_api_key": "test"}))
    turn = p.complete(system=SYSTEM_PROMPT, messages=[{"role": "user", "content": "hi"}], tools=definitions())
    assert sent["model"] == "claude-opus-5-5" and sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "tool_choice" not in sent and "temperature" not in sent and "budget_tokens" not in str(sent)
    assert all(
        t["strict"]
        and t["input_schema"]["additionalProperties"] is False
        and set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])
        for t in sent["tools"]
    )
    # thinking blocks come back intact so they can be replayed unchanged
    assert turn.content[0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert turn.tool_calls[0]["name"] == "inspect_project" and turn.stop_reason == "tool_use"
    assert os.environ.get("PARALLEL_ASSISTANT_PROVIDER") is None
