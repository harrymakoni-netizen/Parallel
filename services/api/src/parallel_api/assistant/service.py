"""Assistant conversations: the tool loop, persistence and replay.

History is append-only. Each provider turn is stored exactly as returned
(including thinking blocks) and replayed unchanged, tool results are stored as
the user turns that carried them, and notices (provider errors, refusals) are
shown to the user but never sent back to the model.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select

from parallel_core.access import AccessDenied
from parallel_core.models import AssistantMessage, AssistantSession, AssistantToolCall, Organisation, Project
from parallel_core.settings import get_settings

from ..errors import ApiError
from .providers import ProviderError, get_provider
from .tools import TOOLS, ToolContext, ToolFailure, definitions

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the assistant inside Parallel, a platform that captures real places, prepares simulation models and runs \
established engines (JuPedSim, SUMO, SWMM, EPANET, SimPy, OpenFOAM, EnergyPlus and others) so people can compare \
scenarios. You work inside one project, as the signed-in user, only through the tools provided. You cannot run code \
or query databases directly, and the tools enforce the user's permissions.

How to work:
- Inspect the project first and identify the model and revision a request concerns.
- To draft a scenario, read the scenario schema for the discipline, use identifiers from the model, and explain the \
proposed changes. When an input is missing, say what is missing instead of inventing a value.
- Scenario drafts and report narratives you create are suggestions. They are used only after a user adopts them in \
Parallel. Never say that a draft has been adopted or has run unless a tool result shows it.
- Explain results only from recorded run results. For every number you report, cite the metric identifier and the \
run identifier (for example "evac.t100 = 56.3 s on run 3f2a..."). If a metric is unavailable or unattained, say so; \
never estimate it.
- Never infer rock strength from appearance, hidden drainage geometry from surface photographs, a unique vehicle \
speed from aftermath images, traffic demand from road width, or material fire properties from colour.
- In CrashLens work keep observations, interpretations and hypotheses distinct. Never determine fault and never give \
probabilities that a hypothesis is true.
- If a tool says an action is not permitted, tell the user plainly.

Keep responses focused and concise: short paragraphs or lists, with identifiers where they matter."""


def availability(db, project: Project) -> dict[str, Any]:
    org = db.get(Organisation, project.organisation_id)
    if not (org and (org.settings or {}).get("allow_external_ai")):
        return {
            "available": False,
            "reason": "Your organisation has not enabled the AI assistant.",
            "provider": None,
            "model": None,
        }
    p = get_provider()
    ok, reason = p.availability()
    return {"available": ok, "reason": reason, "provider": p.name, "model": p.model}


def _next_seq(db, session_id: str) -> int:
    a = (
        db.scalar(select(func.max(AssistantMessage.seq)).where(AssistantMessage.session_id == session_id))
        or 0
    )
    b = (
        db.scalar(select(func.max(AssistantToolCall.seq)).where(AssistantToolCall.session_id == session_id))
        or 0
    )
    return max(a, b) + 1


def _add_message(db, session_id: str, role: str, content: Any, usage: dict | None = None) -> AssistantMessage:
    m = AssistantMessage(
        session_id=session_id, seq=_next_seq(db, session_id), role=role, content=content, usage=usage or {}
    )
    db.add(m)
    db.flush()
    return m


def history(db, session_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.session_id == session_id)
        .order_by(AssistantMessage.seq)
    ).all()
    out = []
    for m in rows:
        if m.role == "assistant":
            out.append({"role": "assistant", "content": m.content})
        elif m.role in ("user", "tool_results"):
            out.append({"role": "user", "content": m.content})
    return out


def _execute(ctx: ToolContext, name: str, args: dict) -> tuple[dict, bool, bool]:
    """(result, is_error, authorised). Handlers validate before writing, so a failure leaves no partial change."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}"}, True, True
    try:
        return tool.handler(ctx, args), False, True
    except AccessDenied as e:
        return {"error": f"Not permitted for this user: {e}"}, True, False
    except ToolFailure as e:
        return {"error": str(e)}, True, True
    except ApiError as e:
        return {"error": e.message}, True, e.status not in (401, 403, 404)
    except Exception:
        log.exception("assistant tool %s failed", name)
        return {"error": "The tool failed unexpectedly; the failure has been logged."}, True, True


def respond(db, session: AssistantSession, user, project: Project, text: str) -> list[int]:
    """Run one user turn to completion. Returns the sequence numbers created in this turn."""
    status = availability(db, project)
    if not status["available"]:
        raise ApiError(503, "assistant_unavailable", status["reason"])
    provider = get_provider()
    first = _next_seq(db, session.id)
    _add_message(db, session.id, "user", [{"type": "text", "text": text}])
    messages = history(db, session.id)
    for _round in range(get_settings().assistant_max_tool_rounds):
        try:
            turn = provider.complete(system=SYSTEM_PROMPT, messages=messages, tools=definitions())
        except ProviderError as e:
            _add_message(db, session.id, "notice", {"kind": "provider_error", "text": str(e)})
            break
        _add_message(db, session.id, "assistant", turn.content, {**turn.usage, "served_by": turn.model})
        messages.append({"role": "assistant", "content": turn.content})
        if turn.stop_reason == "refusal":
            _add_message(
                db,
                session.id,
                "notice",
                {"kind": "refusal", "text": "The model declined this request.", **(turn.refusal or {})},
            )
            break
        if turn.stop_reason == "max_tokens":
            _add_message(
                db,
                session.id,
                "notice",
                {"kind": "truncated", "text": "The response reached its length limit; no tool was run."},
            )
            break
        calls = turn.tool_calls
        if turn.stop_reason != "tool_use" or not calls:
            break
        results = []
        for call in calls:
            ctx = ToolContext(
                db=db,
                user=user,
                project=project,
                session_id=session.id,
                tool_use_id=call["id"],
                provider=session.provider,
                model=session.model,
            )
            result, is_error, authorised = _execute(ctx, call["name"], dict(call.get("input") or {}))
            db.add(
                AssistantToolCall(
                    session_id=session.id,
                    seq=_next_seq(db, session.id),
                    tool_use_id=call["id"],
                    tool_name=call["name"],
                    arguments=call.get("input") or {},
                    result=result,
                    authorised=authorised,
                    error=result.get("error") if is_error else None,
                    source_refs=ctx.source_refs,
                )
            )
            db.flush()
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": json.dumps(result, default=str),
                    **({"is_error": True} if is_error else {}),
                }
            )
        _add_message(db, session.id, "tool_results", results)
        messages.append({"role": "user", "content": results})
    else:
        _add_message(
            db,
            session.id,
            "notice",
            {"kind": "round_limit", "text": "Stopped after the maximum number of tool rounds."},
        )
    return list(range(first, _next_seq(db, session.id)))


def render(db, session: AssistantSession, since_seq: int = 0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in db.scalars(
        select(AssistantMessage).where(
            AssistantMessage.session_id == session.id, AssistantMessage.seq >= since_seq
        )
    ):
        if m.role == "user":
            items.append(
                {
                    "seq": m.seq,
                    "type": "user",
                    "text": "\n".join(b.get("text", "") for b in m.content if b.get("type") == "text"),
                }
            )
        elif m.role == "assistant":
            text = "\n\n".join(
                b.get("text", "") for b in m.content if b.get("type") == "text" and b.get("text")
            )
            if text:
                items.append(
                    {
                        "seq": m.seq,
                        "type": "assistant",
                        "text": text,
                        "served_by": (m.usage or {}).get("served_by"),
                        "fallback": any(b.get("type") == "fallback" for b in m.content),
                    }
                )
        elif m.role == "notice":
            items.append({"seq": m.seq, "type": "notice", **m.content})
    for c in db.scalars(
        select(AssistantToolCall).where(
            AssistantToolCall.session_id == session.id, AssistantToolCall.seq >= since_seq
        )
    ):
        tool = TOOLS.get(c.tool_name)
        items.append(
            {
                "seq": c.seq,
                "type": "tool_call",
                "id": c.id,
                "tool": c.tool_name,
                "arguments": c.arguments,
                "result": c.result,
                "authorised": c.authorised,
                "error": c.error,
                "source_refs": c.source_refs,
                "suggestion": bool(tool and tool.suggestion and not c.error),
                "adopted_by": c.adopted_by,
                "adopted_at": c.adopted_at,
                "adopted_object_id": c.adopted_object_id,
            }
        )
    return sorted(items, key=lambda x: x["seq"])
