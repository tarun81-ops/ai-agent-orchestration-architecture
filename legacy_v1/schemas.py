from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

VALID_AGENTS = ("researcher", "writer", "tester")
AgentRole = Literal["researcher", "writer", "tester"]
Status = Literal["running", "passed", "max_rounds", "failed"]


class Step(BaseModel):
    id: str
    agent: AgentRole
    instruction: str
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("agent")
    @classmethod
    def validate_agent(cls, v: str) -> str:
        if v not in VALID_AGENTS:
            raise ValueError(f"agent must be one of: {', '.join(VALID_AGENTS)}")
        return v


class Plan(BaseModel):
    steps: list[Step]
    checklist: list[str]

    @model_validator(mode="after")
    def validate_plan(self) -> Plan:
        # At least 1 step
        if not self.steps:
            raise ValueError("Plan must contain at least 1 step")

        # Checklist not empty
        if not self.checklist:
            raise ValueError("Checklist must not be empty")

        # Unique step IDs, valid agent, and depends_on may only reference earlier steps
        seen_ids: set[str] = set()
        for step in self.steps:
            if step.id in seen_ids:
                raise ValueError(f"Duplicate step id found: '{step.id}'")

            if step.agent not in VALID_AGENTS:
                raise ValueError(
                    f"Agent for step '{step.id}' must be one of {VALID_AGENTS}, got '{step.agent}'"
                )

            for dep in step.depends_on:
                if dep not in seen_ids:
                    raise ValueError(
                        f"Step '{step.id}' depends on '{dep}', which must reference an earlier step"
                    )

            seen_ids.add(step.id)

        return self


class Issue(BaseModel):
    step_id: str
    problem: str
    fix: str


class Verdict(BaseModel):
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    summary: str = ""


class StepResult(BaseModel):
    output: str
    attempts: int = 1
    history: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def handle_previous_outputs_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "history" not in data and "previous_outputs" in data:
                data["history"] = data.pop("previous_outputs")
        return data

    @property
    def previous_outputs(self) -> list[str]:
        return self.history


class RunState(BaseModel):
    run_id: str
    task: str
    plan: Plan | None = None
    results: dict[str, StepResult] = Field(default_factory=dict)
    verdicts: list[Verdict] = Field(default_factory=list)
    rounds_used: int = 0
    status: Status = "running"
    final_output: str | None = None
