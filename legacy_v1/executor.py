from __future__ import annotations

from typing import Any, Callable, Collection

from config import MAX_CONTEXT_CHARS
from prompts import AGENTS
from schemas import RunState, Step, StepResult
import state as state_module


class StepError(Exception):
    """Raised when a step fails to execute after retries."""
    pass


def build_user_message(step: Step, state: RunState, feedback: Any = None) -> str:
    """Build the user message prompt for a step.

    Includes the original task, the step instruction, and the outputs of ONLY
    the steps listed in step.depends_on, each labelled with its step id and agent.
    Dependency outputs longer than MAX_CONTEXT_CHARS are truncated with '[truncated]'.
    If feedback is given, includes previous output and feedback, directing revision.
    """
    parts: list[str] = [
        f"Original Task:\n{state.task}",
        f"Current Step: {step.id} ({step.agent})\nInstruction:\n{step.instruction}",
    ]

    # Map of step_id -> Step to find dependent agents
    step_map = {s.id: s for s in state.plan.steps} if state.plan else {}

    if step.depends_on:
        dep_sections: list[str] = []
        for dep_id in step.depends_on:
            dep_step = step_map.get(dep_id)
            dep_agent = dep_step.agent if dep_step else "unknown"
            dep_result = state.results.get(dep_id)
            output_text = dep_result.output if dep_result else ""

            if len(output_text) > MAX_CONTEXT_CHARS:
                output_text = output_text[:MAX_CONTEXT_CHARS] + "\n[truncated]"

            dep_sections.append(f"--- Output of Step {dep_id} ({dep_agent}) ---\n{output_text}")

        parts.append("Dependency Outputs:\n" + "\n\n".join(dep_sections))

    if feedback is not None:
        problem = (
            getattr(feedback, "problem", None)
            or (feedback.get("problem") if isinstance(feedback, dict) else str(feedback))
        )
        fix = (
            getattr(feedback, "fix", None)
            or (feedback.get("fix") if isinstance(feedback, dict) else "")
        )
        prev_output = state.results[step.id].output if step.id in state.results else ""

        feedback_section = (
            f"Previous Output for Revision:\n{prev_output}\n\n"
            f"Reviewer Feedback:\n"
            f"- Problem: {problem}\n"
            f"- Recommended Fix: {fix}\n\n"
            f"Instruction for Revision:\n"
            f"Please revise your previous output based on the reviewer's feedback. "
            f"Do not start over from scratch; revise and improve the existing output."
        )
        parts.append(feedback_section)

    return "\n\n".join(parts)


def run_step(
    step: Step,
    state: RunState,
    llm: Callable[..., Any],
    feedback: Any = None,
) -> StepResult:
    """Run an individual step using the specified agent and prompt.

    Calls llm(step.agent, AGENTS[step.agent].system_prompt, message) and stores
    the output in state.results. On revision, appends old output to history and
    increments attempts. Retries once on exception, then saves state, logs error,
    and raises StepError.
    """
    message = build_user_message(step, state, feedback=feedback)
    agent_info = AGENTS[step.agent]
    system_prompt = getattr(agent_info, "system_prompt", agent_info.get("system"))

    try:
        raw_output = llm(step.agent, system_prompt, message)
    except Exception as first_exc:
        state_module.log(state, f"Step '{step.id}' ({step.agent}) failed: {first_exc}. Retrying once...")
        try:
            raw_output = llm(step.agent, system_prompt, message)
        except Exception as second_exc:
            error_msg = f"Step '{step.id}' ({step.agent}) failed after retry: {second_exc}"
            state_module.log(state, error_msg)
            state_module.save_state(state)
            raise StepError(error_msg) from second_exc

    output = str(raw_output)

    if step.id in state.results:
        old_result = state.results[step.id]
        history = list(old_result.history) + [old_result.output]
        attempts = old_result.attempts + 1
        result = StepResult(output=output, attempts=attempts, history=history)
    else:
        result = StepResult(output=output, attempts=1, history=[])

    state.results[step.id] = result
    return result


def run_plan(
    state: RunState,
    llm: Callable[..., Any],
    only_steps: Collection[str] | None = None,
    feedbacks: dict[str, Any] | None = None,
) -> RunState:
    """Execute steps in plan order sequentially.

    Saves state and logs after every step, printing a progress line per step.
    Skips steps that already have a result unless they are in only_steps,
    enabling resumption of interrupted runs.
    """
    if not state.plan:
        raise ValueError("Cannot run_plan: state has no plan")

    only_set = set(only_steps) if only_steps is not None else None

    for step in state.plan.steps:
        if only_set is not None:
            if step.id not in only_set:
                continue
        elif step.id in state.results:
            continue

        instr_preview = (
            step.instruction
            if len(step.instruction) <= 120
            else step.instruction[:117] + "..."
        )
        print(f"Running step {step.id} ({step.agent}): {instr_preview}")
        feedback = feedbacks.get(step.id) if feedbacks else None
        run_step(step, state, llm, feedback=feedback)
        state_module.save_state(state)
        state_module.log(state, f"Completed step {step.id} ({step.agent})")

    if state.plan.steps:
        last_step_id = state.plan.steps[-1].id
        if last_step_id in state.results:
            state.final_output = state.results[last_step_id].output
            state_module.save_state(state)

    return state
