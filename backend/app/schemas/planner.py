"""Request/response models for the planning endpoint.

Planning is a short conversation: the caller sends a goal, the planner either
asks clarifying questions or returns a validated workflow. Answers to the
questions are sent back with the original goal on the next call.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.workflow import ValidationReport, Workflow


class ClarificationAnswer(BaseModel):
    """One answered clarifying question, echoed back to the planner."""

    question: str
    answer: str


class PlanRequest(BaseModel):
    """Input to ``POST /workflow/plan``."""

    goal: str = Field(..., min_length=1, description="The user's natural-language goal.")
    clarifications: list[ClarificationAnswer] = Field(default_factory=list)


class ClarificationQuestion(BaseModel):
    """A question the planner needs answered before it can plan."""

    id: str
    question: str
    why: str | None = Field(default=None, description="Why the answer is needed.")


class PlanResult(BaseModel):
    """Output of ``POST /workflow/plan``.

    * ``needs_input`` - answer ``questions`` and call again.
    * ``planned``     - ``workflow`` is set; check ``validation.ok`` (the planner
                        returns its best effort even if repair attempts ran out).
    """

    status: Literal["needs_input", "planned"]
    goal: str
    backend: str
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    workflow: Workflow | None = None
    validation: ValidationReport | None = None
    attempts: int = 0
    notes: list[str] = Field(default_factory=list)
