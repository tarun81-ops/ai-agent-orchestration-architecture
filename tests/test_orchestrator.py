import json
import pytest

from orchestrator import PlanError, make_plan
from prompts import AGENTS, PLANNER_SYSTEM
from schemas import Plan


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, role, system, user, json_mode=False):
        self.calls.append(
            {
                "role": role,
                "system": system,
                "user": user,
                "json_mode": json_mode,
            }
        )
        if not self.responses:
            raise RuntimeError("No more fake responses available")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_prompts_structure():
    assert set(AGENTS.keys()) == {"researcher", "writer", "tester"}
    for role, info in AGENTS.items():
        assert "description" in info and len(info["description"]) > 0
        assert "system" in info and len(info["system"]) > 0

    assert "JSON" in PLANNER_SYSTEM
    assert "researcher" in PLANNER_SYSTEM
    assert "writer" in PLANNER_SYSTEM
    assert "tester" in PLANNER_SYSTEM
    assert "depends_on" in PLANNER_SYSTEM
    assert "checklist" in PLANNER_SYSTEM


def test_valid_plan_works():
    valid_data = {
        "steps": [
            {
                "id": "step_1",
                "agent": "researcher",
                "instruction": "Gather facts on topic",
                "depends_on": [],
            },
            {
                "id": "step_2",
                "agent": "writer",
                "instruction": "Draft deliverable",
                "depends_on": ["step_1"],
            },
        ],
        "checklist": [
            "Includes all relevant facts",
            "Follows clear structure",
            "No grammatical issues",
        ],
    }

    fake_llm = FakeLLM([valid_data])
    plan = make_plan("Write a summary", llm=fake_llm)

    assert isinstance(plan, Plan)
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "step_1"
    assert plan.steps[1].depends_on == ["step_1"]
    assert len(plan.checklist) == 3

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["role"] == "orchestrator"
    assert call["system"] == PLANNER_SYSTEM
    assert call["user"] == "Write a summary"
    assert call["json_mode"] is True


def test_invalid_then_valid_triggers_one_retry_with_error_sent_back():
    invalid_data = {
        "steps": [],  # Violates 'at least 1 step'
        "checklist": ["Must be complete"],
    }
    valid_data = {
        "steps": [
            {
                "id": "step_1",
                "agent": "researcher",
                "instruction": "Research topic",
                "depends_on": [],
            }
        ],
        "checklist": ["Must be complete"],
    }

    fake_llm = FakeLLM([invalid_data, valid_data])
    plan = make_plan("Research topic", llm=fake_llm)

    assert isinstance(plan, Plan)
    assert len(fake_llm.calls) == 2  # exactly 1 retry triggered

    # Initial call
    assert fake_llm.calls[0]["user"] == "Research topic"

    # Retry call must have received the error text from validation failure
    retry_prompt = fake_llm.calls[1]["user"]
    assert "Research topic" in retry_prompt
    assert "at least 1 step" in retry_prompt


def test_invalid_json_string_then_valid_json_string():
    invalid_json = "NOT_JSON_DATA"
    valid_json = json.dumps(
        {
            "steps": [
                {
                    "id": "s1",
                    "agent": "writer",
                    "instruction": "Write code",
                    "depends_on": [],
                }
            ],
            "checklist": ["Code runs", "Tests pass", "Documented"],
        }
    )

    fake_llm = FakeLLM([invalid_json, valid_json])
    plan = make_plan("Write some code", llm=fake_llm)

    assert isinstance(plan, Plan)
    assert len(fake_llm.calls) == 2
    assert "JSONDecodeError" in fake_llm.calls[1]["user"] or "Expecting value" in fake_llm.calls[1]["user"]


def test_always_invalid_raises_plan_error():
    invalid_data = {
        "steps": [
            {
                "id": "s1",
                "agent": "unknown_agent",  # Invalid agent
                "instruction": "Invalid step",
                "depends_on": [],
            }
        ],
        "checklist": ["Criteria 1"],
    }

    fake_llm = FakeLLM([invalid_data, invalid_data, invalid_data])

    with pytest.raises(PlanError) as exc_info:
        make_plan("Do something invalid", llm=fake_llm)

    # Initial attempt + 2 retries = 3 total calls
    assert len(fake_llm.calls) == 3
    assert "Failed to generate a valid plan after 2 retries" in str(exc_info.value)
    assert "agent" in str(exc_info.value)
