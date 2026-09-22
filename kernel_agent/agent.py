"""The kernel agent: orchestrator + specialists + review loop, built with Google ADK.

The flow is deterministic - ADK runs these in the order we list them:

    planner            -> writes state["plan"]      (the orchestrator's plan + checklist)
    researcher         -> writes state["research"]  (facts, using the Firecrawl tools)
    writer             -> writes state["draft"]     (the draft deliverable)
    tester             -> writes state["result"]    (the final deliverable)

    refine_loop  (LoopAgent, max_iterations=3)
        reviewer       -> every checklist item satisfied? call exit_loop -> the loop stops
                       -> otherwise write state["review"] with what must be fixed
        refiner        -> reads {result} and {review}, then rewrites state["result"]

Agents never talk to each other directly: ADK hands each agent only the state keys
its instruction asks for, using {state_key} templating. That replaces the v1
executor.py, which copied only the outputs named in a step's depends_on list.

Run it (from the project folder):
    .venv\\Scripts\\adk.exe run kernel_agent "Explain how a capacitor stores charge"
    .venv\\Scripts\\adk.exe run kernel_agent          (interactive chat)
    .venv\\Scripts\\adk.exe web                       (browser UI, then pick kernel_agent)
Offline demo with no API key: set KERNEL_MOCK=1 first (see mock_agents.py).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# Load keys before anything else. Both the project .env and the copy inside this
# package are read, so editing either file works; values already loaded win.
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE.parent / ".env")
load_dotenv(_HERE / ".env")

# "your_key_here" is the placeholder from .env.example. If that is what we picked
# up, look in the other file for a real key instead of failing with "API key not
# valid" later on.
_PLACEHOLDER = "your_key_here"
if os.getenv("GOOGLE_API_KEY", "").strip() in ("", _PLACEHOLDER):
    for _candidate in (_HERE.parent / ".env", _HERE / ".env"):
        _value = (dotenv_values(_candidate).get("GOOGLE_API_KEY") or "").strip()
        if _value and _value != _PLACEHOLDER:
            os.environ["GOOGLE_API_KEY"] = _value
            break

# The ADK CLI itself loads kernel_agent\.env when it starts this agent, and a later
# .env load wins. So if that copy is still a placeholder, say it clearly instead of
# letting you hit "API key not valid" with no explanation.
if (_HERE / ".env").exists():
    _agent_env_key = (dotenv_values(_HERE / ".env").get("GOOGLE_API_KEY") or "").strip()
    if _agent_env_key in ("", _PLACEHOLDER):
        print(
            "[!] kernel_agent\\.env has no real GOOGLE_API_KEY. The ADK CLI loads that file "
            "for this agent, so put your key there too - run_kernel.bat copies .env across "
            "for you automatically."
        )

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent  # noqa: E402
from google.adk.tools import exit_loop  # noqa: E402
from google.adk.workflow import RetryConfig  # noqa: E402

from . import prompts  # noqa: E402
from .tools import scrape_page, search_web  # noqa: E402

# ADK 2.x marks SequentialAgent and LoopAgent as deprecated in favour of its newer
# Workflow graph. They still work and are by far the simplest way to build a fixed
# pipeline like this one, so we keep them and silence just those two warnings.
warnings.filterwarnings(
    "ignore", message="SequentialAgent is deprecated", category=DeprecationWarning
)
warnings.filterwarnings("ignore", message="LoopAgent is deprecated", category=DeprecationWarning)

# Which Gemini model every agent uses. Override in .env: KERNEL_MODEL=gemini-3.6-flash
MODEL = os.getenv("KERNEL_MODEL", "gemini-3.6-flash")

# KERNEL_MOCK=1 builds the whole tree out of fake agents, so the pipeline can be
# demoed and tested with no API key and no network at all.
USE_MOCK = os.getenv("KERNEL_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}

# Free API keys regularly hit "429 rate limited" or "503 model is busy". ADK can
# retry an agent for us, with growing waits, instead of failing the whole run.
RETRY = RetryConfig(
    max_attempts=4,
    initial_delay=2.0,
    max_delay=30.0,
    backoff_factor=2.0,
    jitter=1.0,
)


def build_real_agents() -> SequentialAgent:
    """Build the Gemini-backed agent tree (planner + specialists + refine loop)."""
    # --- the orchestrator: turns the task into a plan plus a checklist ---------
    planner = LlmAgent(
        name="planner",
        model=MODEL,
        retry_config=RETRY,
        description="Breaks the user's task into an ordered plan plus a verification checklist.",
        instruction=prompts.PLANNER_INSTRUCTION,
        output_key="plan",  # -> state["plan"], read by everyone with {plan}
    )

    # --- the specialists, in the order ADK will run them ----------------------
    researcher = LlmAgent(
        name="researcher",
        model=MODEL,
        retry_config=RETRY,
        description=prompts.RESEARCHER_DESCRIPTION,
        instruction=prompts.RESEARCHER_INSTRUCTION,  # reads {plan}
        tools=[search_web, scrape_page],  # Firecrawl, reused from v1
        output_key="research",
    )

    writer = LlmAgent(
        name="writer",
        model=MODEL,
        retry_config=RETRY,
        description=prompts.WRITER_DESCRIPTION,
        instruction=prompts.WRITER_INSTRUCTION,  # reads {plan} and {research}
        output_key="draft",
    )

    tester = LlmAgent(
        name="tester",
        model=MODEL,
        retry_config=RETRY,
        description=prompts.TESTER_DESCRIPTION,
        instruction=prompts.TESTER_INSTRUCTION,  # reads {plan}, {research}, {draft}
        output_key="result",  # the final deliverable that the loop reviews
    )

    return _build_pipeline(planner, researcher, writer, tester)


def _build_pipeline(planner, researcher, writer, tester) -> SequentialAgent:
    """Add the review-and-revise loop and wrap everything in one SequentialAgent."""
    # --- the review-and-revise loop ------------------------------------------
    reviewer = LlmAgent(
        name="reviewer",
        model=MODEL,
        retry_config=RETRY,
        description=prompts.REVIEWER_DESCRIPTION,
        instruction=prompts.REVIEWER_INSTRUCTION,  # reads {plan} and {result}
        tools=[exit_loop],  # the model calls this when everything passes
        output_key="review",  # feedback for the refiner
    )

    refiner = LlmAgent(
        name="refiner",
        model=MODEL,
        retry_config=RETRY,
        description=prompts.REFINER_DESCRIPTION,
        instruction=prompts.REFINER_INSTRUCTION,  # reads {plan}, {result}, {review}
        output_key="result",  # overwrites the deliverable with the improved version
    )

    refine_loop = LoopAgent(
        name="refine_loop",
        description="Reviews the deliverable and has the refiner fix it, up to 3 rounds.",
        # Reviewer first: round 1 reviews the tester's result, round 2 reviews the
        # refiner's fix. The loop stops early as soon as exit_loop is called.
        sub_agents=[reviewer, refiner],
        max_iterations=3,
    )

    return SequentialAgent(
        name="kernel_agent",
        description="Plans a task, runs researcher/writer/tester in order, then reviews and revises.",
        sub_agents=[planner, researcher, writer, tester, refine_loop],
    )


if USE_MOCK:
    # Offline demo mode (KERNEL_MOCK=1): same tree, fake agents, no API key.
    from .mock_agents import build_mock_agents  # imported only in mock mode

    # Say it in the console too: it is easy to leave KERNEL_MOCK set in a terminal
    # window and then wonder why the answers look canned.
    print("[*] KERNEL_MOCK is on: using fake agents, no API calls will be made.")
    root_agent = build_mock_agents()
else:
    if not os.getenv("GOOGLE_API_KEY"):
        # Not fatal (ADK prints its own error) but this explains the fix clearly.
        print(
            "[!] GOOGLE_API_KEY is not set. Add it to .env (see .env.example) "
            "or run with KERNEL_MOCK=1 for the offline demo."
        )
    root_agent = build_real_agents()
