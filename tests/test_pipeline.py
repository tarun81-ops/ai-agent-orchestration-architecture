from pathlib import Path
from typing import Any
import pytest

from mock_llm import MockLLM
from pipeline import run_task
from schemas import Issue, Plan, RunState, Step, StepResult, Verdict
import state as state_module


class ScriptedPipelineLLM:
    def __init__(self, plan_data, step_responses, review_verdicts):
        self.plan_data = plan_data
        self.step_responses = step_responses  # dict: role -> list of responses
        self.review_verdicts = list(review_verdicts)
        self.call_counts = {
            "orchestrator": 0,
            "researcher": 0,
            "writer": 0,
            "tester": 0,
            "reviewer": 0,
        }
        self.calls = []

    def __call__(self, role: str, system: str, user: str, json_mode: bool = False) -> Any:
        self.calls.append({"role": role, "system": system, "user": user, "json_mode": json_mode})
        self.call_counts[role] += 1

        if role == "orchestrator":
            return self.plan_data

        elif role == "reviewer":
            if not self.review_verdicts:
                raise RuntimeError("Exhausted scripted review verdicts")
            return self.review_verdicts.pop(0)

        elif role in self.step_responses:
            resps = self.step_responses[role]
            if isinstance(resps, list):
                if not resps:
                    return f"Default output for {role}"
                return resps.pop(0)
            return resps

        return f"Output for {role}"


def test_pipeline_passes_in_round_1(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan_data = {
        "steps": [
            {
                "id": "s1",
                "agent": "researcher",
                "instruction": "Research topic",
                "depends_on": [],
            },
            {
                "id": "s2",
                "agent": "writer",
                "instruction": "Draft article",
                "depends_on": ["s1"],
            },
        ],
        "checklist": ["Well researched", "Clear article"],
    }

    step_responses = {
        "researcher": ["Research facts"],
        "writer": ["Article draft"],
    }

    review_verdicts = [
        {
            "passed": True,
            "issues": [],
            "summary": "Flawless on round 1",
        }
    ]

    llm = ScriptedPipelineLLM(plan_data, step_responses, review_verdicts)

    state = run_task(task="Write article", llm=llm, max_rounds=3)

    assert state.status == "passed"
    assert state.rounds_used == 1
    assert len(state.verdicts) == 1
    assert state.verdicts[0].passed is True
    assert state.final_output == "Article draft"

    assert llm.call_counts["orchestrator"] == 1
    assert llm.call_counts["researcher"] == 1
    assert llm.call_counts["writer"] == 1
    assert llm.call_counts["reviewer"] == 1
    assert llm.call_counts["tester"] == 0


def test_pipeline_fails_once_then_passes_exact_call_counts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan_data = {
        "steps": [
            {
                "id": "s1",
                "agent": "researcher",
                "instruction": "Collect facts",
                "depends_on": [],
            },
            {
                "id": "s2",
                "agent": "writer",
                "instruction": "Write report",
                "depends_on": ["s1"],
            },
            {
                "id": "s3",
                "agent": "tester",
                "instruction": "Verify report",
                "depends_on": ["s2"],
            },
        ],
        "checklist": ["Accurate facts", "Clean writing", "Verified quality"],
    }

    step_responses = {
        "researcher": ["Initial Facts"],
        "writer": ["Draft 1 (incomplete)", "Draft 2 (revised and complete)"],
        "tester": ["Verification 1", "Verification 2 (all checks passed)"],
    }

    review_verdicts = [
        # Round 1 fails: names s2 as faulty
        {
            "passed": False,
            "issues": [
                {
                    "step_id": "s2",
                    "problem": "Draft is incomplete",
                    "fix": "Complete all sections",
                }
            ],
            "summary": "Draft needs revision",
        },
        # Round 2 passes
        {
            "passed": True,
            "issues": [],
            "summary": "All checks passed in round 2",
        },
    ]

    llm = ScriptedPipelineLLM(plan_data, step_responses, review_verdicts)

    state = run_task(task="Comprehensive report", llm=llm, max_rounds=3)

    assert state.status == "passed"
    assert state.rounds_used == 2
    assert len(state.verdicts) == 2

    # Assert exact call counts per role:
    # orchestrator: 1
    # researcher: 1 (s1 was NOT re-run since it doesn't depend on s2)
    # writer: 2 (s2 was faulty and was re-run)
    # tester: 2 (s3 depends on s2 and was re-run)
    # reviewer: 2 (round 1 + round 2)
    assert llm.call_counts["orchestrator"] == 1
    assert llm.call_counts["researcher"] == 1
    assert llm.call_counts["writer"] == 2
    assert llm.call_counts["tester"] == 2
    assert llm.call_counts["reviewer"] == 2

    # Verify history in state.results
    assert state.results["s1"].attempts == 1
    assert state.results["s2"].attempts == 2
    assert state.results["s2"].history == ["Draft 1 (incomplete)"]
    assert state.results["s3"].attempts == 2
    assert state.final_output == "Verification 2 (all checks passed)"


def test_pipeline_never_passes_returns_best_attempt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    plan_data = {
        "steps": [
            {
                "id": "s1",
                "agent": "writer",
                "instruction": "Draft document",
                "depends_on": [],
            }
        ],
        "checklist": ["Criterion 1", "Criterion 2", "Criterion 3"],
    }

    step_responses = {
        "writer": [
            "Attempt 1 (poor)",
            "Attempt 2 (best attempt with minor flaw)",
            "Attempt 3 (worse attempt)",
        ]
    }

    # 3 failing verdicts:
    # Round 1 has 2 issues
    # Round 2 has 1 issue (BEST)
    # Round 3 has 3 issues
    review_verdicts = [
        {
            "passed": False,
            "issues": [
                {"step_id": "s1", "problem": "P1", "fix": "F1"},
                {"step_id": "s1", "problem": "P2", "fix": "F2"},
            ],
            "summary": "Round 1 failed with 2 issues",
        },
        {
            "passed": False,
            "issues": [
                {"step_id": "s1", "problem": "Minor flaw", "fix": "Fix minor issue"},
            ],
            "summary": "Round 2 failed with 1 issue",
        },
        {
            "passed": False,
            "issues": [
                {"step_id": "s1", "problem": "P1", "fix": "F1"},
                {"step_id": "s1", "problem": "P2", "fix": "F2"},
                {"step_id": "s1", "problem": "P3", "fix": "F3"},
            ],
            "summary": "Round 3 failed with 3 issues",
        },
    ]

    llm = ScriptedPipelineLLM(plan_data, step_responses, review_verdicts)

    state = run_task(task="Strict task", llm=llm, max_rounds=3)

    assert state.status == "max_rounds"
    assert state.rounds_used == 3

    # Best attempt was Round 2 (1 issue vs 2 and 3)
    assert state.final_output == "Attempt 2 (best attempt with minor flaw)"
    assert state.results["s1"].output == "Attempt 2 (best attempt with minor flaw)"
    assert len(state.verdicts[-1].issues) == 1
    assert state.verdicts[-1].issues[0].problem == "Minor flaw"


def test_pipeline_with_mock_llm(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    llm = MockLLM()
    state = run_task(task="Test mock llm run", llm=llm, max_rounds=3)

    assert state.status == "passed"
    assert state.rounds_used == 2
    assert "Final Verification Report" in (state.final_output or "")
    assert len(state.verdicts) == 2
    assert state.verdicts[0].passed is False
    assert state.verdicts[1].passed is True


def test_pipeline_per_call_logging(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)

    llm = MockLLM()
    state = run_task(task="Logging test task", llm=llm, max_rounds=2)

    log_path = tmp_path / state.run_id / "log.txt"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")

    # Check for per-call metrics: role, seconds taken, output length
    assert "LLM call [orchestrator]" in content
    assert "LLM call [researcher]" in content
    assert "LLM call [writer]" in content
    assert "LLM call [tester]" in content
    assert "LLM call [reviewer]" in content
    assert "took" in content
    assert "output length:" in content
    assert "chars" in content
