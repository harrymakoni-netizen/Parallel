"""PDF rendering of a report manifest (reportlab platypus)."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from parallel_metrics.registry import METRICS

from .charts import INK, INK_2, MUTED, Series, line_chart, sample

LOGO = Path(__file__).parent / "assets" / "parallel-logo.webp"
NAVY = HexColor("#03193a")
RULE = HexColor("#e1e0d9")
CALLOUT = HexColor("#f4f3ef")

_base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK)
S = {
    "title": ParagraphStyle(
        "title", parent=_base, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=NAVY
    ),
    "subtitle": ParagraphStyle("subtitle", parent=_base, fontSize=10, textColor=INK_2, spaceAfter=8),
    "h1": ParagraphStyle(
        "h1",
        parent=_base,
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=NAVY,
        spaceBefore=12,
        spaceAfter=5,
    ),
    "h2": ParagraphStyle(
        "h2", parent=_base, fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=8, spaceAfter=3
    ),
    "body": _base,
    "small": ParagraphStyle("small", parent=_base, fontSize=7.5, leading=10, textColor=INK_2),
    "cell": ParagraphStyle("cell", parent=_base, fontSize=7.5, leading=9.5),
    "cellb": ParagraphStyle("cellb", parent=_base, fontName="Helvetica-Bold", fontSize=7.5, leading=9.5),
    "mono": ParagraphStyle("mono", parent=_base, fontName="Courier", fontSize=6.8, leading=9),
    "bullet": ParagraphStyle("bullet", parent=_base, leftIndent=10, bulletIndent=2),
}
STATUS_TEXT = {"ok": "OK", "warning": "Warning", "info": "Info", "error": "Incompatible"}


def p(text: Any, style: str = "body") -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)), S[style])


def num(v: Any, sig: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v != 0 and (abs(v) >= 1e9 or abs(v) < 1e-3):
            return f"{v:.3e}"
        return format(v, f",.{sig}g")
    return str(v)


def table(
    rows: list[list[Any]], widths: list[float], header: bool = True, mono_cols: tuple[int, ...] = ()
) -> Table:
    data = []
    for r_i, row in enumerate(rows):
        cells = []
        for c_i, v in enumerate(row):
            if isinstance(v, Paragraph):
                cells.append(v)
            elif header and r_i == 0:
                cells.append(p(v, "cellb"))
            else:
                cells.append(p(v, "mono" if c_i in mono_cols else "cell"))
        data.append(cells)
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style.append(("LINEBELOW", (0, 0), (-1, 0), 0.8, INK_2))
    t.setStyle(TableStyle(style))
    return t


def callout(title: str, text: str) -> Table:
    t = Table([[p(title, "cellb")], [p(text, "cell")]], colWidths=[174 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), CALLOUT),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, NAVY),
            ]
        )
    )
    return t


def _when(iso: str | None) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def chart_block(title: str, series: list[Series], x_label: str, y_label: str, x_unit: str, note: str) -> list:
    if not series:
        return []
    shown = series[:4]
    at, rows = sample(shown)
    tbl = [[f"Series \\ {x_label}"] + [f"{num(x, 3)} {x_unit}" for x in at]]
    tbl += [[s.name] + [num(v, 4) for v in r] for s, r in zip(shown, rows, strict=True)]
    width = 174 * mm
    first = 46 * mm
    out = [
        p(title, "h2"),
        line_chart(shown, x_label=x_label, y_label=y_label, width=width, height=62 * mm),
        Spacer(1, 3),
        table(tbl, [first] + [(width - first) / len(at)] * len(at)),
        p(
            note
            + (
                f" Showing the first {len(shown)} of {len(series)} runs; all values are in metrics.csv."
                if len(series) > len(shown)
                else ""
            ),
            "small",
        ),
    ]
    return [KeepTogether(out)]


SUFFIX = {"_s": " (s)", "_m": " (m)", "_mm": " (mm)", "_h": " (h)", "_sha256": " SHA-256"}


def humanise(key: str) -> str:
    for suf, rep in SUFFIX.items():
        if key.endswith(suf):
            key = key[: -len(suf)] + rep
            break
    key = key.replace("_", " ")
    return key[:1].upper() + key[1:]


def metric_label(metric_id: str, entity: str | None) -> str:
    name = METRICS[metric_id].name if metric_id in METRICS else metric_id
    return name + (f" [{entity}]" if entity else "")


def _flat_summary(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    for k, v in summary.items():
        if isinstance(v, dict):
            v = ", ".join(f"{a}: {num(b)}" for a, b in list(v.items())[:12]) + (" …" if len(v) > 12 else "")
        elif isinstance(v, list):
            v = ", ".join(map(str, v[:12])) + (" …" if len(v) > 12 else "")
        else:
            v = num(v)
        rows.append([humanise(k), v])
    return rows


def render_pdf(m: dict[str, Any], charts: list[dict[str, Any]], figures: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    rep = m["report"]
    title = rep["title"]

    def first_page(c, doc):
        c.saveState()
        if LOGO.exists():
            w = 30 * mm
            c.drawImage(
                ImageReader(str(LOGO)), 18 * mm, A4[1] - 22 * mm, width=w, height=w * 809 / 1942, mask="auto"
            )
        footer(c, doc)
        c.restoreState()

    def footer(c, doc):
        c.setFont("Helvetica", 7)
        c.setFillColor(MUTED)
        c.drawString(18 * mm, 10 * mm, f"Parallel · {title[:80]} · report {rep['id']}")
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=26 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=(rep.get("author") or {}).get("name", ""),
        subject=f"Parallel {rep['kind']} report",
        creator="Parallel",
    )
    W = 174 * mm
    story: list = [
        p(title, "title"),
        p(f"{m['project']['name']} · {rep['kind'].capitalize()} report", "subtitle"),
    ]
    facts = [
        ["Project", f"{m['project']['name']} ({m['project']['type'].replace('_', ' ')})"],
        ["Generated", _when(rep["generated_at"])],
        ["Author", (rep.get("author") or {}).get("name", "—")],
        ["Review status", rep["review_status"]],
        ["Report identifier", rep["id"]],
        ["Runs included", ", ".join(f"{s['ref']} = {s['run_id']}" for s in m["runs"]) or "—"],
    ]
    story.append(table(facts, [38 * mm, W - 38 * mm], header=False))
    story.append(Spacer(1, 6))
    if any(s["sources"]["contains_synthetic"] for s in m["runs"]) or m["project"].get("demo_key"):
        story += [
            callout(
                "Synthetic demonstration material",
                "Some inputs in this report are synthetic demonstration material. The results were produced "
                "by real engine runs on those inputs; they do not describe a real site or event.",
            ),
            Spacer(1, 6),
        ]
    if m.get("case"):
        story += [callout("Scope of this report", m["case"]["statement"]), Spacer(1, 6)]
        if m["case"].get("training"):
            story += [callout("Training case", "This case is marked as a training case."), Spacer(1, 6)]

    # ---------------------------------------------------------------- runs and revisions
    if m["runs"]:
        story.append(p("Runs, revisions and engines", "h1"))
        rows = [
            ["Ref", "Scenario (revision)", "Model (revision)", "Engine / adapter", "Mode", "Seed", "Finished"]
        ]
        for s in m["runs"]:
            rows.append(
                [
                    s["ref"],
                    f"{s['scenario']['name']} (rev {s['scenario']['revision_number']})",
                    f"{s['domain_model']['name']} (rev {s['domain_model']['revision_number']})",
                    f"{s['engine']['engine_id']} {s['engine']['engine_version'] or ''} / "
                    f"adapter {s['engine']['adapter_version']}",
                    s["execution_mode"].replace("_", " "),
                    num(s["seed"]),
                    _when(s["finished_at"]),
                ]
            )
        story.append(table(rows, [10 * mm, 38 * mm, 34 * mm, 38 * mm, 18 * mm, 12 * mm, 24 * mm]))
        story.append(Spacer(1, 4))
        ids = [["Ref", "Scenario revision", "Model revision", "Scene revision"]]
        ids += [
            [
                s["ref"],
                s["scenario"]["revision_id"],
                s["domain_model"]["revision_id"],
                s["scene_revision_id"] or "—",
            ]
            for s in m["runs"]
        ]
        story.append(table(ids, [10 * mm, 54.6 * mm, 54.6 * mm, 54.6 * mm], mono_cols=(1, 2, 3)))
        for s in m["runs"]:
            if s.get("imported"):
                imp = s["imported"]
                story.append(
                    p(
                        f"{s['ref']} was imported from an external installation: {imp.get('external_installation')}. "
                        f"Operator provenance: {imp.get('supplied_provenance')}. Package SHA-256 "
                        f"{imp.get('package_sha256')}.",
                        "small",
                    )
                )

    # ---------------------------------------------------------------- comparison basis
    comp = m.get("comparison")
    if comp:
        story.append(p("Comparison basis", "h1"))
        story.append(
            p(
                f"Baseline: {comp.get('baseline_ref')} ({comp['baseline_run_id']}). {comp.get('summary') or ''}"
            )
        )
        story.append(Spacer(1, 3))
        rows = [["Check", "Status", "Detail"]]
        rows += [[c["check"], STATUS_TEXT.get(c["status"], c["status"]), c["detail"]] for c in comp["checks"]]
        story.append(table(rows, [36 * mm, 20 * mm, W - 56 * mm]))

    # ---------------------------------------------------------------- inputs, sources and assumptions
    if m["runs"]:
        story.append(p("Inputs, sources and assumptions", "h1"))
    for s in m["runs"]:
        block = [p(f"{s['ref']} · {s['scenario']['name']}", "h2")]
        summ = _flat_summary(s["input_summary"])
        if summ:
            block.append(table([["Input summary", "Value"], *summ], [50 * mm, W - 50 * mm]))
        if s["model_change_notes"]:
            block.append(Spacer(1, 3))
            block.append(p("How the scenario changed the model:", "small"))
            block += [Paragraph(escape(n), S["bullet"], bulletText="•") for n in s["model_change_notes"]]
        src = s["sources"]
        counts = (
            ", ".join(f"{src['labels'][k]}: {v}" for k, v in src["counts"].items()) or "no provenance records"
        )
        block.append(Spacer(1, 3))
        block.append(p(f"Provenance of recorded inputs — {counts}.", "small"))
        notes = [it for it in src["notable"] if it.get("note")]
        if src["scenario_assumptions"] or notes:
            rows = [["Kind", "Statement", "Applies to"]]
            rows += [
                [
                    "scenario assumption",
                    a["statement"] + (f" ({a['rationale']})" if a.get("rationale") else ""),
                    ", ".join(a["applies_to"]) or "—",
                ]
                for a in src["scenario_assumptions"]
            ]
            rows += [
                [
                    it["kind"].replace("_", " "),
                    it["note"] + (f" — source: {it['source']}" if it.get("source") else ""),
                    it["path"],
                ]
                for it in notes[:14]
            ]
            block.append(table(rows, [28 * mm, 96 * mm, 50 * mm]))
            if len(notes) > 14:
                block.append(
                    p(f"{len(notes) - 14} further source notes are listed in manifest.json.", "small")
                )
        story += block

    # ---------------------------------------------------------------- results
    story.append(p("Results", "h1"))
    if comp:
        base = comp.get("baseline_ref") or "baseline"
        for name, ref in (comp.get("candidate_refs") or {}).items():
            rows = [["Metric", "Unit", f"Baseline ({base})", f"{ref or name}", "Abs. diff", "% diff", "Note"]]
            for r in comp["table"]:
                c = r["candidates"].get(name)
                if c is None:
                    continue
                label = metric_label(r["metric_id"], r["entity"])
                b = r["baseline"]
                bv = num(b["value"]) if b["status"] == "available" else b["status"]
                cv = num(c["value"]) if c["status"] == "available" else c["status"]
                pct = f"{c['pct_diff']:+.1f}%" if c.get("pct_diff") is not None else "—"
                ad = f"{c['abs_diff']:+,.4g}" if c.get("abs_diff") is not None else "—"
                unit = "ratio" if r["unit"] == "1" else (r["unit"] or "")
                rows.append([label, unit, bv, cv, ad, pct, c.get("diff_note") or ""])
            story.append(p(f"{name} ({ref}) against the baseline", "h2"))
            story.append(table(rows, [44 * mm, 13 * mm, 22 * mm, 22 * mm, 18 * mm, 14 * mm, 41 * mm]))
    else:
        for s in m["runs"]:
            rows = [["Metric", "Entity", "Value", "Unit", "Status", "Note"]]
            rows += [
                [
                    x["name"],
                    x["entity"] or "—",
                    num(x["value"]),
                    x["unit"] if x["unit"] != "1" else "ratio",
                    x["status"],
                    x["note"] or "",
                ]
                for x in s["metrics"]
            ]
            story.append(p(f"{s['ref']} · {s['scenario']['name']}", "h2"))
            story.append(table(rows, [52 * mm, 24 * mm, 20 * mm, 14 * mm, 20 * mm, 44 * mm]))
    for ch in charts:
        story += chart_block(**ch)

    # ---------------------------------------------------------------- figures (scene screenshots)
    if figures:
        story.append(p("Figures", "h1"))
        for f in figures:
            img = ImageReader(io.BytesIO(f["data"]))
            iw, ih = img.getSize()
            w = min(W, iw)
            story.append(
                KeepTogether(
                    [
                        Image(io.BytesIO(f["data"]), width=w, height=w * ih / iw),
                        p(f"{f['caption']} — asset {f['asset_id']}, SHA-256 {f['sha256']}", "small"),
                    ]
                )
            )

    # ---------------------------------------------------------------- case sections
    case = m.get("case")
    if case:
        story.append(p(f"Case {case['reference']}: {case['title']}", "h1"))
        story.append(p(case["media_policy"], "small"))
        rows = [["Evidence ID", "Kind", "Title", "Original SHA-256", "Claimed capture"]]
        rows += [
            [e["id"], e["kind"], e["title"], e["original_sha256"] or "—", _when(e["claimed_captured_at"])]
            for e in case["evidence"]
        ]
        story += [
            p("Evidence references", "h2"),
            table(rows, [30 * mm, 18 * mm, 42 * mm, 54 * mm, 30 * mm], mono_cols=(0, 3)),
        ]
        for key, label in (
            ("observations", "Observations (what the evidence shows)"),
            ("interpretations", "Interpretations (analyst reasoning)"),
        ):
            rows = [["ID", "Statement", "Measurement", "Limitation", "Review"]]
            rows += [
                [
                    o["id"],
                    o["text"],
                    f"{num(o['measurement_value'])} {o['measurement_unit'] or ''}".strip()
                    if o["measurement_value"] is not None
                    else "—",
                    o["limitation"] or "—",
                    o["review_status"],
                ]
                for o in case[key]
            ]
            story += [
                p(label, "h2"),
                table(rows, [30 * mm, 70 * mm, 24 * mm, 32 * mm, 18 * mm], mono_cols=(0,)),
            ]
        rows = [["Hypothesis ID", "Label", "Status", "Supporting", "Contradicting", "Evidence fit"]]
        for h in case["hypotheses"]:
            sup = [li["observation_id"] for li in h["links"] if li["relation"] == "supports"]
            con = [li["observation_id"] for li in h["links"] if li["relation"] == "contradicts"]
            fit = h.get("evidence_fit") or {}
            rows.append(
                [
                    h["id"],
                    h["label"],
                    h["status"],
                    ", ".join(sup) or "—",
                    ", ".join(con) or "—",
                    fit.get("summary", "not evaluated"),
                ]
            )
        story += [
            p("Hypotheses", "h2"),
            table(rows, [30 * mm, 34 * mm, 14 * mm, 32 * mm, 32 * mm, 32 * mm], mono_cols=(0, 3, 4)),
        ]
        if case["timeline"]:
            rows = [["t (s)", "±", "Event", "Source", "Hypothesis"]]
            rows += [
                [
                    num(t["t_offset_s"]),
                    num(t["t_uncertainty_s"]),
                    t["description"],
                    f"{t['source_type']} {t['source_id'] or ''}".strip(),
                    t["hypothesis_id"] or "—",
                ]
                for t in case["timeline"]
            ]
            story += [
                p("Timeline", "h2"),
                table(rows, [14 * mm, 10 * mm, 80 * mm, 40 * mm, 30 * mm], mono_cols=(4,)),
            ]
        if case["external_analyses"]:
            rows = [["Engine", "Installation", "Analyst", "Method", "Provenance", "Package SHA-256"]]
            rows += [
                [
                    x["engine_id"],
                    x["external_installation"],
                    x["analyst"],
                    x["method"],
                    x["supplied_provenance"],
                    x["package_sha256"] or "—",
                ]
                for x in case["external_analyses"]
            ]
            story += [
                p("External analyses", "h2"),
                table(rows, [18 * mm, 30 * mm, 22 * mm, 36 * mm, 36 * mm, 32 * mm], mono_cols=(5,)),
            ]

    # ---------------------------------------------------------------- warnings, narrative, reviews, traceability
    story.append(p("Warnings", "h1"))
    if m["warnings"]:
        story += [Paragraph(escape(w), S["bullet"], bulletText="•") for w in m["warnings"]]
    else:
        story.append(p("No warnings were recorded for the runs in this report."))
    if m.get("narrative"):
        n = m["narrative"]
        story.append(p("Narrative", "h1"))
        origin = (
            "Drafted with AI assistance and adopted by the author."
            if n["origin"] == "ai_draft_adopted"
            else "Written by the author."
        )
        story += [p(origin, "small"), p(n["text"])]
    story.append(p("Reviews", "h1"))
    if m["reviews"]:
        rows = [["Object", "Scope", "Status", "Comment", "Date"]]
        rows += [
            [
                f"{r['object_type']} {r['object_id']}",
                r["scope"],
                r["status"],
                r["comment"] or "—",
                _when(r["at"]),
            ]
            for r in m["reviews"]
        ]
        story.append(table(rows, [52 * mm, 28 * mm, 24 * mm, 42 * mm, 28 * mm]))
    else:
        story.append(p("No scoped reviews have been recorded for these runs or revisions."))
    story.append(p("Traceability", "h1"))
    story.append(p(m["traceability"]))
    story.append(
        p(
            "The accompanying manifest.json lists every input and output hash; metrics.csv lists every "
            "metric value with its run identifier.",
            "small",
        )
    )
    doc.build(story, onFirstPage=first_page, onLaterPages=footer)
    return buf.getvalue()
