from __future__ import annotations

import json
import time
from typing import Any, Callable

from config import MAX_ROUNDS
from executor import run_plan, run_step
from orchestrator import make_plan
from reviewer import review
from schemas import RunState
from state import load_state, log, new_run_id, save_state

try:
    from llm import call_llm
except ImportError:
    from Llm import call_llm


def make_timed_llm(llm: Callable[..., Any], state: RunState) -> Callable[..., Any]:
    """Wrap an LLM callable to record per-call metrics (role, elapsed seconds, output length) in log.txt."""

    def timed_llm(role: str, system: str, user: str, json_mode: bool = False) -> Any:
        start_time = time.perf_counter()
        resp = llm(role, system, user, json_mode=json_mode)
        elapsed = time.perf_counter() - start_time
        out_repr = json.dumps(resp) if isinstance(resp, (dict, list)) else str(resp)
        log(
            state,
            f"LLM call [{role}] took {elapsed:.2f}s (output length: {len(out_repr)} chars)",
        )
        return resp

    return timed_llm


def run_task(
    task: str | None = None,
    llm: Callable[..., Any] = call_llm,
    max_rounds: int = MAX_ROUNDS,
    run_id: str | None = None,
) -> RunState:
    """Execute a complete task through planning, execution, and iterative review.

    Steps:
    1. make_plan: creates steps and checklist.
    2. run_plan: executes steps in order.
    3. review loop (up to max_rounds): checks results against checklist.
       - If passed, sets status "passed" and stops.
       - If failed, re-runs faulty steps with feedback and re-runs transitive
         dependent steps in plan order, then reviews again.
    4. If max_rounds reached without passing, returns the attempt with fewest
       issues (latest wins ties) and sets status "max_rounds".
    """
    # 1. Initialize or resume state
    if run_id is not None:
        try:
            state = load_state(run_id)
            if task and task != state.task:
                state.task = task
            log(state, f"Resuming run {run_id}")
        except FileNotFoundError:
            if not task:
                raise ValueError(f"Run ID '{run_id}' not found and no task provided.")
            state = RunState(run_id=run_id, task=task)
            save_state(state)
            log(state, f"Started new run {run_id} for task: {task}")
    else:
        if not task:
            raise ValueError("A task string must be provided for a new run.")
        run_id = new_run_id()
        state = RunState(run_id=run_id, task=task)
        save_state(state)
        log(state, f"Started new run {run_id} for task: {task}")

    # Wrap llm with per-call timing and output length logger
    active_llm = make_timed_llm(llm, state)

    # 2. Planning
    if state.plan is None:
        print(f"Creating plan for task: {state.task}")
        state.plan = make_plan(state.task, llm=active_llm)
        save_state(state)
        log(
            state,
            f"Plan created with {len(state.plan.steps)} steps and {len(state.plan.checklist)} checklist items",
        )

    # 3. Initial execution
    print("Executing initial plan...")
    run_plan(state, llm=active_llm)
    save_state(state)
    log(state, "Initial plan execution completed")

    # 4. Review loop
    attempts_history: list[dict[str, Any]] = []

    for round_idx in range(1, max_rounds + 1):
        state.rounds_used = round_idx
        print(f"\n--- Review Round {round_idx}/{max_rounds} ---")
        log(state, f"Starting review round {round_idx}/{max_rounds}")

        verdict = review(state, llm=active_llm)
        state.verdicts.append(verdict)
        save_state(state)
        log(
            state,
            f"Round {round_idx} verdict: passed={verdict.passed}, {len(verdict.issues)} issues",
        )

        # Track snapshot of this round
        snapshot = {
            "round": round_idx,
            "final_output": state.final_output,
            "results": dict(state.results),
            "verdict": verdict,
            "issue_count": len(verdict.issues),
        }
        attempts_history.append(snapshot)

        if verdict.passed:
            state.status = "passed"
            save_state(state)
            log(state, f"Task passed successfully in round {round_idx}!")
            return state

        # If not passed and we have more rounds, re-run faulty steps and dependents
        if round_idx < max_rounds:
            faulty_step_ids = {issue.step_id for issue in verdict.issues}
            feedbacks = {issue.step_id: issue for issue in verdict.issues}

            # Transitive closure of dependent steps in plan order
            steps_to_rerun = set(faulty_step_ids)
            for step in state.plan.steps:
                if any(dep in steps_to_rerun for dep in step.depends_on):
                    steps_to_rerun.add(step.id)

            log(
                state,
                f"Round {round_idx} failed. Re-running steps {sorted(steps_to_rerun)} in round {round_idx + 1}",
            )

            for step in state.plan.steps:
                if step.id in steps_to_rerun:
                    instr_preview = (
                        step.instruction
                        if len(step.instruction) <= 120
                        else step.instruction[:117] + "..."
                    )
                    print(f"Re-running step {step.id} ({step.agent}): {instr_preview}")
                    fb = feedbacks.get(step.id)
                    run_step(step, state, llm=active_llm, feedback=fb)
                    save_state(state)
                    log(state, f"Re-ran step {step.id} ({step.agent})")

            # Update final output from last step
            if state.plan.steps:
                last_step_id = state.plan.steps[-1].id
                if last_step_id in state.results:
                    state.final_output = state.results[last_step_id].output
                    save_state(state)

    # If loop finishes without passing, max_rounds reached
    state.status = "max_rounds"

    # Select attempt with fewest issues (latest wins ties)
    best_attempt = None
    for attempt in attempts_history:
        if best_attempt is None or attempt["issue_count"] <= best_attempt["issue_count"]:
            best_attempt = attempt

    if best_attempt is not None:
        state.final_output = best_attempt["final_output"]
        state.results = best_attempt["results"]
        if state.verdicts and state.verdicts[-1] != best_attempt["verdict"]:
            state.verdicts[-1] = best_attempt["verdict"]

    save_state(state)
    best_round_num = best_attempt["round"] if best_attempt else "unknown"
    best_issues = best_attempt["issue_count"] if best_attempt else "unknown"
    log(
        state,
        f"Max rounds reached ({max_rounds}). Best attempt: Round {best_round_num} with {best_issues} open issues.",
    )
    return state
