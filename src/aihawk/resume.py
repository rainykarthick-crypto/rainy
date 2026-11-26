"""A CV written for one job, when writing one is worth it, and the same CV when it is not.

An employer's system reads the CV before a person does, and it is looking for the words the posting uses. This rewrites
the CV in those words — reordered, re-emphasised, never a fact added — and keeps the result for that job. `auto` asks the
model whether the rewrite is worth anything for this posting at all; `always` rewrites every time; `off` sends the CV as
it is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from aihawk.llm import structured

TAILOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["needed", "reason", "cv"],
    "properties": {
        "needed": {"type": "boolean", "description": "Whether rewriting would make this CV fit this posting better."},
        "reason": {"type": "string", "description": "One short sentence: why, or why not."},
        "cv": {"type": "string", "description": "The whole CV rewritten for this posting, or empty when not needed."},
    },
}

TAILOR_SYSTEM = """\
You rewrite a candidate's CV for one job posting, so that an employer's screening system finds in it what the posting asks
for. You are given the CV and the posting.

Rules, all of them strict:
- Every fact stays exactly as the CV has it: employers, titles, dates, degrees, certificates, numbers. You may reorder
  sections, reorder bullets inside a section, reword a bullet in the posting's own vocabulary, and rewrite the summary to
  name the role. You may not add a skill, tool, employer, date, qualification or number the CV does not contain, and you
  may not imply experience the CV does not show.
- Keep the CV's layout: a name line, contact line, then sections with `##` headings and `- ` bullets.
- Keep it to one page's worth: the same length as the CV given, not longer.
- If the CV already says what this posting asks for, or the posting asks for something the CV plainly does not have, set
  needed to false and leave cv empty: the CV as it is, is the better CV.
"""


def slug_of(job_id: str) -> str:
    """A job's address as a filename: lower case, runs of anything else as one dash."""
    return re.sub(r"[^a-z0-9]+", "-", job_id.lower()).strip("-")[-120:] or "job"


def cache_path(folder: Path, job_id: str) -> Path:
    """Where one job's rewritten CV is kept: the job's address, made into a filename."""
    return folder / f"{slug_of(job_id)}.json"


async def tailored_for(
    client: AsyncOpenAI, model: str, cv: str, posting: str, job_id: str, folder: Path, mode: str,
) -> tuple[str | None, str]:
    """The CV rewritten for this posting, or None to send it as it is; and why, in one line, for the log."""
    if mode == "off":
        return None, ""
    kept = cache_path(folder, job_id)
    if kept.is_file():
        try:
            stored = json.loads(kept.read_text(encoding="utf-8"))
            return (stored.get("cv") or None), str(stored.get("reason") or "kept from an earlier run")
        except (ValueError, OSError):
            kept.unlink(missing_ok=True)
    asking = (
        f"The CV:\n\n{cv}\n\n---\n\nThe posting:\n\n{posting[:12000]}\n\n"
        + ("Rewrite the CV for this posting." if mode == "always"
           else "Say whether rewriting the CV for this posting is worth anything, and rewrite it only if it is.")
    )
    answer = await structured(client, model, TAILOR_SYSTEM, asking, "tailored_cv", TAILOR_SCHEMA)
    needed = bool(answer.get("needed"))
    rewritten = str(answer.get("cv") or "").strip() if needed else ""
    reason = str(answer.get("reason") or "").strip()[:200]
    folder.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps({"needed": needed, "reason": reason, "cv": rewritten}, ensure_ascii=False), encoding="utf-8")
    return (rewritten or None), reason


def _lines(markdown: str) -> list[tuple[str, str]]:
    """The CV as the PDF draws it: (kind, text), where kind is name, contact, heading, bullet or text."""
    out: list[tuple[str, str]] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            out.append(("name", line[2:].strip()))
        elif line.startswith("## "):
            out.append(("heading", line[3:].strip()))
        elif line.startswith("### "):
            out.append(("heading", line[4:].strip()))
        elif re.match(r"^\s*[-*•]\s+", line):
            out.append(("bullet", re.sub(r"^\s*[-*•]\s+", "", line).strip()))
        elif out and out[-1][0] == "name":
            out.append(("contact", line.strip()))
        else:
            out.append(("text", line.strip()))
    return out


def render_pdf(markdown: str, out: Path, title: str) -> None:
    """The CV as a clean one-page PDF: the name over a contact line, then headings and bullets."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate

    name_style = ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=17, leading=20, spaceAfter=2)
    contact_style = ParagraphStyle("contact", fontName="Helvetica", fontSize=9, leading=12, textColor="#444444",
                                   spaceAfter=8, alignment=TA_LEFT)
    heading_style = ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=9,
                                   spaceAfter=3, textColor="#1a1a1a")
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=2)
    bullet_style = ParagraphStyle("bullet", parent=body_style, spaceAfter=1)

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=title, author="")
    story: list[Any] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            story.append(ListFlowable(
                [ListItem(Paragraph(text, bullet_style), leftIndent=10) for text in pending],
                bulletType="bullet", start="•", leftIndent=10, bulletFontSize=8,
            ))
            pending.clear()

    for kind, text in _lines(markdown):
        if kind != "bullet":
            flush()
        if kind == "name":
            story.append(Paragraph(text, name_style))
        elif kind == "contact":
            story.append(Paragraph(text, contact_style))
        elif kind == "heading":
            story.append(Paragraph(text.upper(), heading_style))
        elif kind == "bullet":
            pending.append(text)
        else:
            story.append(Paragraph(text, body_style))
    flush()
    if not story:
        story = [Paragraph(title, name_style)]
    doc.build(story)
