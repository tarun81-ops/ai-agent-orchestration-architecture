from pathlib import Path
import pytest

from config import MAX_CONTEXT_CHARS
from prompts import REVIEWER_SYSTEM
from reviewer import ReviewError, build_review_message, review
from schemas import Issue, Plan, RunState, Step, StepResult, Verdict


class FakeReviewLLM:
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
            raise RuntimeError("No more fake responses")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_build_review_message_structure():
    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Task 1", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Task 2", depends_on=["s1"]),
        ],
        checklist=["Criterion 1", "Criterion 2"],
    )
    state = RunState(
        run_id="r1",
        task="Review task",
        plan=plan,
        results={
            "s1": StepResult(output="Output 1", attempts=1, history=[]),
            "s2": StepResult(output="Output 2 (Deliverable)", attempts=1, history=[]),
        },
        final_output="Output 2 (Deliverable)",
    )

    msg = build_review_message(state)
    assert "Task:\nReview task" in msg
    assert "Criterion 1" in msg
    assert "Criterion 2" in msg
    assert "Output 2 (Deliverable)" in msg
    assert "Step s1 (agent: researcher)" in msg
    assert "Step s2 (agent: writer)" in msg


def test_review_truncation():
    long_text = "Z" * (MAX_CONTEXT_CHARS + 500)
    plan = Plan(
        steps=[Step(id="s1", agent="researcher", instruction="Task 1", depends_on=[])],
        checklist=["Check 1"],
    )
    state = RunState(
        run_id="r_trunc",
        task="Truncation task",
        plan=plan,
        results={"s1": StepResult(output=long_text, attempts=1, history=[])},
    )

    msg = build_review_message(state)
    assert "[truncated]" in msg
    assert long_text not in msg


def test_review_replaces_unknown_step_id_with_last_step_id():
    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Step 1", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Step 2", depends_on=["s1"]),
        ],
        checklist=["Check 1"],
    )
    state = RunState(
        run_id="r_unknown",
        task="Task",
        plan=plan,
        results={
            "s1": StepResult(output="Out 1", attempts=1, history=[]),
            "s2": StepResult(output="Out 2", attempts=1, history=[]),
        },
    )

    # Response mentions unknown step 's99'
    fake_resp = {
        "passed": False,
        "issues": [
            {
                "step_id": "s99",
                "problem": "Unclear text",
                "fix": "Rewrite text",
            }
        ],
        "summary": "Needs revision",
    }
    fake_llm = FakeReviewLLM([fake_resp])

    verdict = review(state, fake_llm)
    assert verdict.passed is False
    assert len(verdict.issues) == 1
    # Should be replaced by the last step id ('s2')
    assert verdict.issues[0].step_id == "s2"


def test_review_retries_and_raises_on_invalid_json():
    plan = Plan(
        steps=[Step(id="s1", agent="researcher", instruction="Task", depends_on=[])],
        checklist=["Check"],
    )
    state = RunState(run_id="r_fail", task="Task", plan=plan)

    fake_llm = FakeReviewLLM(["{invalid_json", "still_invalid", "always_invalid"])

    with pytest.raises(ReviewError) as exc_info:
        review(state, fake_llm)

    # 1 initial call + 2 retries = 3 calls
    assert len(fake_llm.calls) == 3
    assert "Failed to produce a valid Verdict after 2 retries" in str(exc_info.value)
