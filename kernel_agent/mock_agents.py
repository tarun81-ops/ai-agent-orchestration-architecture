"""Fake agents for offline demos (KERNEL_MOCK=1).

Each fake is a small BaseAgent subclass. A BaseAgent only has to implement
`_run_async_impl`, which yields Event objects. The fake writes its canned text
into the session state (exactly what `output_key` does for a real LlmAgent) and
yields one event so the flow looks the same as a real run.

The fake reviewer fails on review round 1 and then sets `escalate=True` on round
2. `escalate` is precisely what the exit_loop tool does, so the LoopAgent stops -
this mirrors the legacy v1 MockLLM, which failed round 1 and passed round 2.
"""

from __future__ import annotations

from typing import AsyncGenerator

from google.adk.agents import BaseAgent, LoopAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

MOCK_PLAN = """TASK: Demo task - explain how a capacitor stores charge.

PLAN:
1. Research the background and technical requirements.
2. Draft the solution deliverable based on the research.
3. Verify, test and polish the final deliverable.

CHECKLIST:
- Covers the fundamental technical requirements
- The draft is clear, concise and structured
- Passes verification and quality checks
"""

MOCK_RESEARCH = """Technical Research Findings (mock):
- Analyzed the architecture and the requirements
- Identified 3 core specifications and constraints
- Verified that the approach is feasible
"""

MOCK_DRAFT = """Draft Deliverable (mock, initial version):
This is the initial draft. It covers the core requirements from the research, but it is
thin: it has no structure and it does not explain the details.
"""

MOCK_RESULT = """Final Deliverable (mock, verified):
This is the verified deliverable produced by the tester agent.
"""

MOCK_REFINED = """Final Deliverable (mock, revised after reviewer feedback):
The reviewer asked for more structure and detail, so this version adds a clear heading,
keeps the facts from the research, and addresses every checklist item.
"""

MOCK_REVIEW_FAIL = """Checklist item 2 ("the draft is clear, concise and structured") fails:
the deliverable has no headings and no explanation of the core specifications.
Fix: add clear section headings and explain each core specification in one sentence.
"""

MOCK_REVIEW_PASS = "Review round 2: PASSED - all checklist criteria are satisfied, exiting the loop."


class FakeStepAgent(BaseAgent):
    """Writes a fixed text into state[reply_key], like an LlmAgent with output_key."""

    reply_key: str = ""
    reply_text: str = ""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        ctx.session.state[self.reply_key] = self.reply_text
        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=self.reply_text)]),
            # state_delta is how ADK records state changes from an event.
            actions=EventActions(state_delta={self.reply_key: self.reply_text}),
        )


class FakeReviewer(BaseAgent):
    """Fails the first review, then escalates (the same thing exit_loop does)."""

    fail_text: str = MOCK_REVIEW_FAIL
    pass_text: str = MOCK_REVIEW_PASS

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        round_number = int(ctx.session.state.get("mock_review_round", 0)) + 1
        ctx.session.state["mock_review_round"] = round_number

        if round_number == 1:
            ctx.session.state["review"] = self.fail_text
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=f"Review round 1: FAILED\n{self.fail_text}")],
                ),
                actions=EventActions(
                    state_delta={"review": self.fail_text, "mock_review_round": round_number}
                ),
            )
        else:
            yield Event(
                author=self.name,
                content=types.Content(role="model", parts=[types.Part(text=self.pass_text)]),
                actions=EventActions(escalate=True, state_delta={"mock_review_round": round_number}),
            )


def build_mock_agents() -> SequentialAgent:
    """The same agent tree as the real one, built from fakes: no API key needed."""
    planner = FakeStepAgent(
        name="planner",
        description="Fake orchestrator: returns a canned plan and checklist.",
        reply_key="plan",
        reply_text=MOCK_PLAN,
    )
    researcher = FakeStepAgent(
        name="researcher",
        description="Fake researcher: returns canned findings (no web calls).",
        reply_key="research",
        reply_text=MOCK_RESEARCH,
    )
    writer = FakeStepAgent(
        name="writer",
        description="Fake writer: returns a canned draft.",
        reply_key="draft",
        reply_text=MOCK_DRAFT,
    )
    tester = FakeStepAgent(
        name="tester",
        description="Fake tester: returns a canned verified deliverable.",
        reply_key="result",
        reply_text=MOCK_RESULT,
    )
    reviewer = FakeReviewer(
        name="reviewer",
        description="Fake reviewer: fails round 1, then exits the loop.",
    )
    refiner = FakeStepAgent(
        name="refiner",
        description="Fake revision worker: returns a canned revised deliverable.",
        reply_key="result",
        reply_text=MOCK_REFINED,
    )

    refine_loop = LoopAgent(
        name="refine_loop",
        description="Reviews the deliverable and has the refiner fix it, up to 3 rounds.",
        sub_agents=[reviewer, refiner],
        max_iterations=3,
    )

    return SequentialAgent(
        name="kernel_agent",
        description="Mock kernel: plan, research, write, test, then review and revise.",
        sub_agents=[planner, researcher, writer, tester, refine_loop],
    )
