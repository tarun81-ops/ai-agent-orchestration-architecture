from __future__ import annotations

import json
import re
from typing import Any, Callable

from config import MAX_CONTEXT_CHARS
from prompts import REVIEWER_SYSTEM
from schemas import RunState, Verdict


class ReviewError(Exception):
    """Raised when the reviewer fails to produce a valid Verdict after retries."""
    pass


def _extract_json(text: str) -> Any:
    """Safely parse JSON from LLM text, stripping markdown blocks if present."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return json.loads(cleaned[start : end + 1])
    return json.loads(text)


def build_review_message(state: RunState) -> str:
    """Construct the review prompt message for the reviewer LLM."""
    parts: list[str] = [
        f"Task:\n{state.task}",
    ]

    checklist_items = state.plan.checklist if state.plan else []
    checklist_str = "\n".join(f"- {c}" for c in checklist_items)
    parts.append(f"Checklist Criteria:\n{checklist_str}")

    last_step_id = state.plan.steps[-1].id if state.plan and state.plan.steps else None
    final_output = state.final_output
    if not final_output and last_step_id and last_step_id in state.results:
        final_output = state.results[last_step_id].output

    if final_output and len(final_output) > MAX_CONTEXT_CHARS:
        final_output = final_output[:MAX_CONTEXT_CHARS] + "\n[truncated]"

    parts.append(f"Final Output (Last Step Output):\n{final_output or ''}")

    step_details: list[str] = []
    if state.plan:
        for step in state.plan.steps:
            res = state.results.get(step.id)
            out = res.output if res else ""
            if len(out) > MAX_CONTEXT_CHARS:
                out = out[:MAX_CONTEXT_CHARS] + "\n[truncated]"
            step_details.append(
                f"--- Step {step.id} (agent: {step.agent}) ---\nOutput:\n{out}"
            )
    parts.append("Step Outputs:\n" + "\n\n".join(step_details))

    return "\n\n".join(parts)


def review(state: RunState, llm: Callable[..., Any]) -> Verdict:
    """Evaluate current run state against the checklist criteria using the reviewer LLM.

    Calls llm("reviewer", REVIEWER_SYSTEM, message, json_mode=True), validating with
    the Verdict model. Retries up to 2 times on invalid JSON, then raises ReviewError.
    Replaces any unknown step_id in issues with the last step's id.
    """
    message = build_review_message(state)
    max_retries = 2
    current_prompt = message
    last_error: Exception | None = None

    valid_step_ids = {s.id for s in state.plan.steps} if state.plan else set()
    last_step_id = state.plan.steps[-1].id if state.plan and state.plan.steps else ""

    for attempt in range(max_retries + 1):
        try:
            raw_response = llm(
                "reviewer",
                REVIEWER_SYSTEM,
                current_prompt,
                json_mode=True,
            )

            if isinstance(raw_response, Verdict):
                verdict = raw_response
            elif isinstance(raw_response, str):
                parsed = _extract_json(raw_response)
                verdict = Verdict.model_validate(parsed)
            elif isinstance(raw_response, dict):
                verdict = Verdict.model_validate(raw_response)
            else:
                raise ValueError(
                    f"Expected dict or JSON string from reviewer LLM, got {type(raw_response).__name__}"
                )

            # Replace any unknown step_id in issues with the last step's id
            for issue in verdict.issues:
                if issue.step_id not in valid_step_ids:
                    issue.step_id = last_step_id

            return verdict

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                current_prompt = (
                    f"{message}\n\n"
                    f"Previous attempt produced an invalid response with error:\n{e}\n"
                    f"Please fix the error and reply with valid JSON matching the Verdict schema."
                )

    raise ReviewError(
        f"Failed to produce a valid Verdict after {max_retries} retries. Last error: {last_error}"
    )
