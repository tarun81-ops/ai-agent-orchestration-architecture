import pytest
from pydantic import ValidationError

from schemas import Issue, Plan, RunState, Step, StepResult, Verdict


def test_valid_step():
    step = Step(
        id="step_1",
        agent="researcher",
        instruction="Gather data",
        depends_on=[],
    )
    assert step.id == "step_1"
    assert step.agent == "researcher"
    assert step.instruction == "Gather data"
    assert step.depends_on == []


def test_step_invalid_agent():
    with pytest.raises(ValidationError):
        Step(id="step_1", agent="orchestrator", instruction="Direct agents")

    with pytest.raises(ValidationError):
        Step(id="step_1", agent="reviewer", instruction="Review agents")

    with pytest.raises(ValidationError):
        Step(id="step_1", agent="unknown", instruction="Do something")


def test_valid_plan():
    step1 = Step(id="s1", agent="researcher", instruction="Find data")
    step2 = Step(id="s2", agent="writer", instruction="Draft text", depends_on=["s1"])
    step3 = Step(id="s3", agent="tester", instruction="Check text", depends_on=["s2"])
    plan = Plan(steps=[step1, step2, step3], checklist=["Covers topic", "Accurate data"])

    assert len(plan.steps) == 3
    assert plan.checklist == ["Covers topic", "Accurate data"]


def test_plan_empty_steps():
    with pytest.raises(ValidationError, match="at least 1 step"):
        Plan(steps=[], checklist=["Criterion 1"])


def test_plan_empty_checklist():
    step = Step(id="s1", agent="researcher", instruction="Task 1")
    with pytest.raises(ValidationError, match="Checklist must not be empty"):
        Plan(steps=[step], checklist=[])


def test_plan_duplicate_step_ids():
    step1 = Step(id="s1", agent="researcher", instruction="Task 1")
    step2 = Step(id="s1", agent="writer", instruction="Task 2")
    with pytest.raises(ValidationError, match="Duplicate step id"):
        Plan(steps=[step1, step2], checklist=["Criterion 1"])


def test_plan_depends_on_must_be_earlier_step():
    # Forward dependency: s1 depends on s2
    step1 = Step(id="s1", agent="researcher", instruction="Task 1", depends_on=["s2"])
    step2 = Step(id="s2", agent="writer", instruction="Task 2")
    with pytest.raises(ValidationError, match="must reference an earlier step"):
        Plan(steps=[step1, step2], checklist=["Criterion 1"])

    # Self dependency: s1 depends on s1
    step_self = Step(id="s1", agent="researcher", instruction="Task 1", depends_on=["s1"])
    with pytest.raises(ValidationError, match="must reference an earlier step"):
        Plan(steps=[step_self], checklist=["Criterion 1"])

    # Non-existent dependency
    step_missing = Step(id="s1", agent="researcher", instruction="Task 1", depends_on=["s0"])
    with pytest.raises(ValidationError, match="must reference an earlier step"):
        Plan(steps=[step_missing], checklist=["Criterion 1"])


def test_issue_model():
    issue = Issue(step_id="s1", problem="Missing sources", fix="Add citation")
    assert issue.step_id == "s1"
    assert issue.problem == "Missing sources"
    assert issue.fix == "Add citation"


def test_verdict_model():
    # Pass verdict
    v_pass = Verdict(passed=True, summary="All checks passed")
    assert v_pass.passed is True
    assert v_pass.issues == []
    assert v_pass.summary == "All checks passed"

    # Fail verdict with issues
    v_fail = Verdict(
        passed=False,
        issues=[Issue(step_id="s2", problem="Formatting issue", fix="Use markdown")],
        summary="Needs revision",
    )
    assert v_fail.passed is False
    assert len(v_fail.issues) == 1
    assert v_fail.issues[0].step_id == "s2"


def test_step_result_model():
    res = StepResult(output="First draft", attempts=1, history=[])
    assert res.output == "First draft"
    assert res.attempts == 1
    assert res.history == []
    assert res.previous_outputs == []

    # Test alias compatibility for previous_outputs
    res2 = StepResult.model_validate(
        {"output": "Second draft", "attempts": 2, "previous_outputs": ["First draft"]}
    )
    assert res2.output == "Second draft"
    assert res2.attempts == 2
    assert res2.history == ["First draft"]
    assert res2.previous_outputs == ["First draft"]


def test_run_state_model():
    state = RunState(run_id="run_123", task="Write an article")
    assert state.run_id == "run_123"
    assert state.task == "Write an article"
    assert state.status == "running"
    assert state.plan is None
    assert state.results == {}
    assert state.verdicts == []
    assert state.rounds_used == 0
    assert state.final_output is None

    # Valid status values
    for valid_status in ["running", "passed", "max_rounds", "failed"]:
        state.status = valid_status
        assert state.status == valid_status

    # Invalid status value
    with pytest.raises(ValidationError):
        RunState(run_id="run_123", task="Task", status="completed")


def test_run_state_json_roundtrip():
    step1 = Step(id="s1", agent="researcher", instruction="Research")
    plan = Plan(steps=[step1], checklist=["Checked"])
    state = RunState(
        run_id="run_456",
        task="Deep analysis",
        plan=plan,
        results={"s1": StepResult(output="Done", attempts=1, history=[])},
        verdicts=[Verdict(passed=True, summary="Good")],
        rounds_used=1,
        status="passed",
        final_output="Done",
    )

    json_data = state.model_dump_json()
    loaded = RunState.model_validate_json(json_data)
    assert loaded.run_id == state.run_id
    assert loaded.task == state.task
    assert loaded.plan.steps[0].id == "s1"
    assert loaded.results["s1"].output == "Done"
    assert loaded.verdicts[0].passed is True
    assert loaded.status == "passed"
    assert loaded.final_output == "Done"
