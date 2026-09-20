import json
from pathlib import Path
import pytest

from schemas import RunState, Step, Plan
from state import load_state, log, new_run_id, save_state


def test_new_run_id():
    id1 = new_run_id()
    id2 = new_run_id()
    assert isinstance(id1, str) and len(id1) > 0
    assert isinstance(id2, str) and len(id2) > 0
    assert id1 != id2


def test_save_and_load_state(tmp_path: Path):
    plan = Plan(
        steps=[Step(id="s1", agent="writer", instruction="Write story")],
        checklist=["Creative", "Has ending"],
    )
    state = RunState(run_id="test_run_01", task="Tell a story", plan=plan)

    # Save state to temporary directory
    state_file = save_state(state, runs_dir=tmp_path)
    assert state_file.exists()
    assert state_file.name == "state.json"
    assert state_file.parent.name == "test_run_01"

    # Verify JSON content is valid utf-8
    raw_content = state_file.read_text(encoding="utf-8")
    data = json.loads(raw_content)
    assert data["run_id"] == "test_run_01"
    assert data["task"] == "Tell a story"

    # Load state
    loaded_state = load_state("test_run_01", runs_dir=tmp_path)
    assert loaded_state.run_id == state.run_id
    assert loaded_state.task == state.task
    assert loaded_state.plan is not None
    assert loaded_state.plan.steps[0].instruction == "Write story"
    assert loaded_state.status == "running"


def test_load_state_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_state("non_existent_run", runs_dir=tmp_path)


def test_log_with_state_and_string(tmp_path: Path):
    state = RunState(run_id="test_run_log", task="Logging task")

    # Log using RunState object
    log_file1 = log(state, "Starting task", runs_dir=tmp_path)
    assert log_file1.exists()
    assert log_file1.name == "log.txt"

    # Log using string run_id
    log_file2 = log("test_run_log", "Finished step 1", runs_dir=tmp_path)
    assert log_file1 == log_file2

    # Verify content
    content = log_file1.read_text(encoding="utf-8")
    assert "Starting task\nFinished step 1\n" == content


def test_default_runs_dir():
    run_id = f"test_default_{new_run_id()}"
    state = RunState(run_id=run_id, task="Default runs dir test")

    saved_path = save_state(state)
    expected_path = Path("runs") / run_id / "state.json"
    assert saved_path.resolve() == expected_path.resolve()
    assert expected_path.exists()

    loaded = load_state(run_id)
    assert loaded.run_id == run_id

    log(state, "Logging to default runs dir")
    log_path = Path("runs") / run_id / "log.txt"
    assert log_path.exists()
    assert "Logging to default runs dir" in log_path.read_text(encoding="utf-8")

    # Clean up test artifact from runs/
    import shutil
    shutil.rmtree(Path("runs") / run_id, ignore_errors=True)
