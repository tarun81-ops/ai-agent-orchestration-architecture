import pytest
from pathlib import Path
from unittest.mock import MagicMock

from config import MAX_CONTEXT_CHARS
from executor import StepError, build_user_message, run_plan, run_step
from schemas import Issue, Plan, RunState, Step, StepResult
import state as state_module


class FakeExecutorLLM:
    def __init__(self, responses_by_agent=None, default_response="Done"):
        self.responses_by_agent = responses_by_agent or {}
        self.default_response = default_response
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
        if role in self.responses_by_agent:
            resp_queue = self.responses_by_agent[role]
            if isinstance(resp_queue, list):
                if not resp_queue:
                    return self.default_response
                resp = resp_queue.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                return resp
            elif isinstance(resp_queue, Exception):
                raise resp_queue
            return resp_queue
        return self.default_response


def test_steps_run_in_order(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Find sources", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Draft paper", depends_on=["s1"]),
            Step(id="s3", agent="tester", instruction="Review paper", depends_on=["s2"]),
        ],
        checklist=["Complete paper", "Accurate sources"],
    )
    state = RunState(run_id="run_order_test", task="Write research paper", plan=plan)

    fake_llm = FakeExecutorLLM(
        responses_by_agent={
            "researcher": ["Sources found"],
            "writer": ["Paper draft"],
            "tester": ["Paper verified"],
        }
    )

    run_plan(state, fake_llm)

    # Verify execution order
    assert len(fake_llm.calls) == 3
    assert fake_llm.calls[0]["role"] == "researcher"
    assert fake_llm.calls[1]["role"] == "writer"
    assert fake_llm.calls[2]["role"] == "tester"

    # Verify results
    assert set(state.results.keys()) == {"s1", "s2", "s3"}
    assert state.results["s1"].output == "Sources found"
    assert state.results["s2"].output == "Paper draft"
    assert state.results["s3"].output == "Paper verified"
    assert state.final_output == "Paper verified"


def test_step_never_sees_outputs_it_does_not_depend_on(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Task 1", depends_on=[]),
            Step(id="s2", agent="researcher", instruction="Task 2", depends_on=[]),
            Step(id="s3", agent="writer", instruction="Task 3", depends_on=["s2"]),
        ],
        checklist=["Done"],
    )
    state = RunState(run_id="run_dep_test", task="Test dependencies", plan=plan)

    fake_llm = FakeExecutorLLM(
        responses_by_agent={
            "researcher": ["SECRET_S1_OUTPUT", "PUBLIC_S2_OUTPUT"],
            "writer": ["Final document"],
        }
    )

    run_plan(state, fake_llm)

    # s3 message should contain s2's output, but never s1's output
    s3_message = fake_llm.calls[2]["user"]
    assert "PUBLIC_S2_OUTPUT" in s3_message
    assert "SECRET_S1_OUTPUT" not in s3_message


def test_long_outputs_are_truncated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Gather lots of data", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Summarize data", depends_on=["s1"]),
        ],
        checklist=["Done"],
    )
    state = RunState(run_id="run_trunc_test", task="Test truncation", plan=plan)

    long_output = "A" * (MAX_CONTEXT_CHARS + 1000)
    fake_llm = FakeExecutorLLM(
        responses_by_agent={
            "researcher": [long_output],
            "writer": ["Summary"],
        }
    )

    run_plan(state, fake_llm)

    s2_message = fake_llm.calls[1]["user"]
    assert "[truncated]" in s2_message
    # Check that the raw 7000-character sequence isn't in s2_message
    assert long_output not in s2_message


def test_state_saved_after_each_step(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Step 1", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Step 2", depends_on=["s1"]),
        ],
        checklist=["Done"],
    )
    state = RunState(run_id="run_save_test", task="Test saving", plan=plan)

    save_snapshots = []
    original_save_state = state_module.save_state

    def spy_save_state(st, runs_dir=tmp_path):
        save_snapshots.append(dict(st.results))
        return original_save_state(st, runs_dir=runs_dir)

    monkeypatch.setattr(state_module, "save_state", spy_save_state)

    fake_llm = FakeExecutorLLM()
    run_plan(state, fake_llm)

    # Initial step 1 saves, then step 2 saves, then final_output saves
    assert len(save_snapshots) >= 2
    assert "s1" in save_snapshots[0]
    assert "s2" not in save_snapshots[0]
    assert "s1" in save_snapshots[1]
    assert "s2" in save_snapshots[1]


def test_resume_skips_finished_steps(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan = Plan(
        steps=[
            Step(id="s1", agent="researcher", instruction="Step 1", depends_on=[]),
            Step(id="s2", agent="writer", instruction="Step 2", depends_on=["s1"]),
        ],
        checklist=["Done"],
    )
    # Simulate s1 already completed
    state = RunState(
        run_id="run_resume_test",
        task="Test resume",
        plan=plan,
        results={"s1": StepResult(output="Pre-existing output", attempts=1, history=[])},
    )

    fake_llm = FakeExecutorLLM(default_response="Output for s2")
    run_plan(state, fake_llm)

    # s1 was skipped, fake_llm only called once for s2
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["role"] == "writer"
    assert state.results["s1"].output == "Pre-existing output"
    assert state.results["s2"].output == "Output for s2"

    # Now run with only_steps specifying s1: s1 should execute and update history/attempts
    fake_llm_only = FakeExecutorLLM(default_response="Updated output for s1")
    run_plan(state, fake_llm_only, only_steps=["s1"])

    assert len(fake_llm_only.calls) == 1
    assert fake_llm_only.calls[0]["role"] == "researcher"
    assert state.results["s1"].output == "Updated output for s1"
    assert state.results["s1"].attempts == 2
    assert state.results["s1"].history == ["Pre-existing output"]


def test_failing_llm_raises_step_error_after_one_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    step = Step(id="s1", agent="researcher", instruction="Fail step", depends_on=[])
    plan = Plan(steps=[step], checklist=["Done"])
    state = RunState(run_id="run_fail_test", task="Test failure", plan=plan)

    call_count = 0

    def failing_llm(role, system, user, json_mode=False):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("LLM network timeout")

    with pytest.raises(StepError) as exc_info:
        run_step(step, state, failing_llm)

    # Initial attempt + 1 retry = 2 calls
    assert call_count == 2
    assert "failed after retry" in str(exc_info.value)

    # Verify state was saved to disk
    state_file = tmp_path / "run_fail_test" / "state.json"
    assert state_file.exists()

    # Verify error was logged
    log_file = tmp_path / "run_fail_test" / "log.txt"
    assert log_file.exists()
    assert "failed after retry" in log_file.read_text(encoding="utf-8")


def test_feedback_revision_instruction_and_history(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    step = Step(id="s1", agent="writer", instruction="Draft post", depends_on=[])
    plan = Plan(steps=[step], checklist=["Done"])
    state = RunState(
        run_id="run_feedback_test",
        task="Write blog post",
        plan=plan,
        results={"s1": StepResult(output="First draft with typos", attempts=1, history=[])},
    )

    feedback = Issue(
        step_id="s1",
        problem="Typos in second paragraph",
        fix="Fix spelling and punctuation",
    )

    msg = build_user_message(step, state, feedback=feedback)
    assert "First draft with typos" in msg
    assert "Typos in second paragraph" in msg
    assert "Fix spelling and punctuation" in msg
    assert "revise" in msg.lower()
    assert "not start over" in msg.lower()

    fake_llm = FakeExecutorLLM(default_response="Clean second draft")
    run_step(step, state, fake_llm, feedback=feedback)

    res = state.results["s1"]
    assert res.output == "Clean second draft"
    assert res.attempts == 2
    assert res.history == ["First draft with typos"]
