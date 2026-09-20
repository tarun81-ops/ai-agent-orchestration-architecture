from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

try:
    from llm import call_llm
except ImportError:
    from Llm import call_llm

from prompts import PLANNER_SYSTEM
from schemas import Plan


class PlanError(Exception):
    """Raised when the orchestrator fails to generate a valid Plan."""
    pass


def _extract_json(text: str) -> Any:
    """Safely parse JSON from LLM text, stripping markdown blocks if present."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return json.loads(cleaned[start : end + 1])
    return json.loads(text)


def make_plan(task: str, llm: Callable[..., Any] = call_llm) -> Plan:
    """Create a validated execution Plan for a task using the orchestrator LLM.

    Calls llm("orchestrator", PLANNER_SYSTEM, task, json_mode=True) and validates
    with the Plan model. On JSON or validation errors, retries up to 2 times,
    appending the error message to the user prompt so the model can fix it.
    After that raises PlanError with a clear message.
    """
    max_retries = 2
    current_prompt = task
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            raw_response = llm(
                "orchestrator",
                PLANNER_SYSTEM,
                current_prompt,
                json_mode=True,
            )

            if isinstance(raw_response, Plan):
                return raw_response

            if isinstance(raw_response, str):
                parsed = _extract_json(raw_response)
            elif isinstance(raw_response, dict):
                parsed = raw_response
            else:
                raise ValueError(
                    f"Expected dict or JSON string from LLM, got {type(raw_response).__name__}"
                )

            plan = Plan.model_validate(parsed)
            return plan

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                current_prompt = (
                    f"{task}\n\n"
                    f"Previous attempt produced an invalid response with error:\n{e}\n"
                    f"Please fix this error and return valid JSON matching the schema."
                )

    raise PlanError(
        f"Failed to generate a valid plan after {max_retries} retries. Last error: {last_error}"
    )
