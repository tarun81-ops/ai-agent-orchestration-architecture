from __future__ import annotations

from typing import Any


class MockLLM:
    """Scripted fake LLM that executes a 3-step plan across 2 review rounds with no network.

    - Returns a valid 3-step plan for the orchestrator.
    - Returns plausible step outputs for researcher, writer, tester.
    - Returns a failing verdict naming step_2 in round 1.
    - Returns a passing verdict in round 2.
    """

    def __init__(self):
        self.orchestrator_call_count = 0
        self.researcher_call_count = 0
        self.writer_call_count = 0
        self.tester_call_count = 0
        self.review_call_count = 0
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        role: str,
        system: str,
        user: str,
        json_mode: bool = False,
    ) -> Any:
        self.calls.append(
            {
                "role": role,
                "system": system,
                "user": user,
                "json_mode": json_mode,
            }
        )

        if role == "orchestrator":
            self.orchestrator_call_count += 1
            return {
                "steps": [
                    {
                        "id": "step_1",
                        "agent": "researcher",
                        "instruction": "Research the background and technical requirements.",
                        "depends_on": [],
                    },
                    {
                        "id": "step_2",
                        "agent": "writer",
                        "instruction": "Draft the solution deliverable based on the research.",
                        "depends_on": ["step_1"],
                    },
                    {
                        "id": "step_3",
                        "agent": "tester",
                        "instruction": "Verify, test, and polish the final deliverable.",
                        "depends_on": ["step_2"],
                    },
                ],
                "checklist": [
                    "Covers fundamental technical requirements",
                    "Draft is clear, concise, and structured",
                    "Passes verification and quality checks",
                ],
            }

        elif role == "researcher":
            self.researcher_call_count += 1
            return (
                "Technical Research Findings:\n"
                "- Analyzed architecture and requirements\n"
                "- Identified 3 core specifications and constraints\n"
                "- Verified system feasibility."
            )

        elif role == "writer":
            self.writer_call_count += 1
            if self.writer_call_count == 1:
                return (
                    "Draft Deliverable (Initial Version):\n"
                    "Here is the initial draft of the deliverable. "
                    "It addresses the core requirements from the research."
                )
            else:
                return (
                    "Draft Deliverable (Revised Version):\n"
                    "Here is the revised deliverable. All reviewer feedback has been incorporated "
                    "with clear structure, detailed explanations, and complete sections."
                )

        elif role == "tester":
            self.tester_call_count += 1
            if self.tester_call_count == 1:
                return (
                    "Test Report (Round 1):\n"
                    "Ran sanity checks on the initial draft. Identified areas needing revision in step 2."
                )
            else:
                return (
                    "Final Verification Report (Round 2):\n"
                    "All verification tests and sanity checks passed. The deliverable is complete."
                )

        elif role == "reviewer":
            self.review_call_count += 1
            if self.review_call_count == 1:
                return {
                    "passed": False,
                    "issues": [
                        {
                            "step_id": "step_2",
                            "problem": "The draft in step 2 is missing detailed explanations and structure.",
                            "fix": "Add detailed explanations for all core specifications and format with clean headers.",
                        }
                    ],
                    "summary": "Step 2 draft did not satisfy the completeness checklist item.",
                }
            else:
                return {
                    "passed": True,
                    "issues": [],
                    "summary": "All checklist criteria are completely satisfied. The deliverable is ready.",
                }

        raise ValueError(f"Unknown role in MockLLM: {role}")


mock_llm = MockLLM()
