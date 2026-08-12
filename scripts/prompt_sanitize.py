"""
Canonical prompt sanitisation, shared by generation and selection.

Why this exists as its own module: the single most important property of a task
prompt in this benchmark is that it describes a SYMPTOM and never points at the
fix. Pytest tracebacks violate that by default -- they print the library frame
where the exception surfaced, which for a body-removal task is literally the
function the agent is supposed to write. Leaving that in would collapse the task
from "localise and repair" to "type the obvious thing at the named location",
and it was measurably happening in 11 of 100 generated prompts before this pass.

The trade-off is acknowledged: real bug reports often do carry tracebacks. We
choose the harder, less realistic variant deliberately, because an unsaturated
benchmark is the goal and localisation is the part current models find hard.
"""

from __future__ import annotations

import re

_LIB_PATH = re.compile(r"\b(?:/\S*/)?pydantic/[A-Za-z0-9_/]+\.py\b")


def redact_locations(text: str) -> str:
    """Remove anything that identifies WHERE the defect lives."""
    text = _LIB_PATH.sub("<library>", text)
    text = re.sub(r"tests?/\S+", "<test>", text)
    text = re.sub(r"/tmp/\S+", "<path>", text)
    text = re.sub(r"\b(test_[A-Za-z0-9_]+)\b", "reproduce", text)
    return text


def redact_problem_statement(ps: str) -> str:
    """
    Apply redaction to the observed-failure block only.

    The instruction text intentionally contains the string ``pydantic/`` (it
    tells the agent where it is allowed to edit); redacting that would make the
    prompt incoherent. So we only touch the fenced blocks.
    """
    parts = ps.split("```")
    for i in range(1, len(parts), 2):
        parts[i] = redact_locations(parts[i])
    return "```".join(parts)
