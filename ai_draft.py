"""Draft contract and local demo generator for the model integration."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

DraftText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000)]


class DraftRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]


class DraftFields(BaseModel):
    goal: DraftText = ""
    done: DraftText = ""
    open_questions: DraftText = ""
    next_steps: DraftText = ""
    evidence: DraftText = ""


class DraftResult(BaseModel):
    mode: Literal["demo", "model"]
    draft: DraftFields


class DraftUnavailableError(Exception):
    """The configured model service could not generate a draft."""


LABELS = {
    "目标": "goal",
    "当前目标": "goal",
    "已完成": "done",
    "未解决问题": "open_questions",
    "下一步": "next_steps",
    "参考依据": "evidence",
}


def generate_draft(text: str) -> DraftResult:
    """Replace this demo with a model adapter that returns the same contract."""
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = re.split(r"[:：]", line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        label, content = (part.strip() for part in parts)
        if label in LABELS and content:
            fields.setdefault(LABELS[label], []).append(content)

    draft = {field: "\n".join(lines) for field, lines in fields.items()}
    draft.setdefault("evidence", text)
    return DraftResult(mode="demo", draft=DraftFields(**draft))
