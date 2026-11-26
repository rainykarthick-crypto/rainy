"""The CV written for a posting: what is rendered, when the model says a rewrite is worth it, and what is kept."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aihawk import resume

CV = "# Ada Rossi\nMilan, Italy | ada@example.com\n## Summary\n- Five years in network operations\n"


def test_a_cv_is_rendered_as_a_pdf(tmp_path: Path) -> None:
    out = tmp_path / "Ada Rossi.pdf"
    resume.render_pdf(CV, out, title="Ada Rossi")
    assert out.read_bytes()[:4] == b"%PDF"
    assert out.stat().st_size > 800


def test_the_lines_of_a_cv_keep_their_kind() -> None:
    kinds = [kind for kind, _ in resume._lines(CV)]
    assert kinds == ["name", "contact", "heading", "bullet"]


def test_a_rewrite_is_asked_for_once_and_then_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []

    async def scripted(*_args: Any, **_keywords: Any) -> dict[str, Any]:
        asked.append(1)
        return {"needed": True, "reason": "closer to the posting", "cv": "# Ada Rossi\nMilan\n## Summary\n- Networks\n"}

    monkeypatch.setattr(resume, "structured", scripted)
    first, reason = asyncio.run(resume.tailored_for(None, "model", CV, "a posting", "job-1", tmp_path, "auto"))
    assert first and reason == "closer to the posting"

    again, _ = asyncio.run(resume.tailored_for(None, "model", CV, "a posting", "job-1", tmp_path, "auto"))
    assert again == first, "the second ask reads the kept rewrite"
    assert len(asked) == 1, "the model was not asked twice for the same job"


def test_a_rewrite_that_is_not_needed_sends_the_cv_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_needed(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"needed": False, "reason": "the CV already says it", "cv": ""}

    monkeypatch.setattr(resume, "structured", not_needed)
    text, reason = asyncio.run(resume.tailored_for(None, "model", CV, "a posting", "job-2", tmp_path, "auto"))
    assert text is None
    assert reason == "the CV already says it"


def test_off_asks_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_args: Any, **_keywords: Any) -> dict[str, Any]:
        raise AssertionError("off must not call the model")

    monkeypatch.setattr(resume, "structured", never)
    text, reason = asyncio.run(resume.tailored_for(None, "model", CV, "a posting", "job-3", tmp_path, "off"))
    assert text is None and reason == ""
