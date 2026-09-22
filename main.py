from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from dotenv import load_dotenv

from config import MAX_ROUNDS, RUNS_DIR
from mock_llm import MockLLM
from pipeline import run_task
from research import build_research_llm

try:
    from llm import call_llm
except ImportError:
    from Llm import call_llm

CONSOLE_MAX_CHARS = 600


def sanitize_message(text: str) -> str:
    """Mask any potential API keys from error messages."""
    return re.sub(
        r"(gsk_[a-zA-Z0-9_-]+|AIza[a-zA-Z0-9_-]+|fc-[a-zA-Z0-9_-]+|AQ\.[a-zA-Z0-9_-]+)",
        "[REDACTED_KEY]",
        text,
    )


def use_safe_console_encoding() -> None:
    """Stop Windows consoles (cp1252) from crashing on model text.

    Model output and scraped web pages routinely contain characters such as the
    non-breaking hyphen (U+2011), en dashes and curly quotes that cp1252 cannot
    encode. Reconfiguring the streams with errors="replace" prints "?" instead of
    raising UnicodeEncodeError part-way through a run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            continue


def handle_backend_error(exc: Exception) -> None:
    """Print friendly actionable hints for backend failures instead of a traceback."""
    err_str = sanitize_message(str(exc))
    err_type = type(exc).__name__

    if "environment variable first" in err_str or "API_KEY" in err_str:
        print("\n[!] Error: Missing API Key.", file=sys.stderr)
        print(
            "    Hint: Set GROQ_API_KEY or GEMINI_API_KEY in your .env file or environment.",
            file=sys.stderr,
        )
        print(f"    Details: {err_str}", file=sys.stderr)
    elif (
        "11434" in err_str
        or "ConnectError" in err_type
        or "ConnectionRefused" in err_str
        or "Failed to connect" in err_str
    ):
        print("\n[!] Error: Cannot connect to Ollama.", file=sys.stderr)
        print(
            "    Hint: Local Ollama is not running on http://localhost:11434.",
            file=sys.stderr,
        )
        print("    Please start the Ollama service: 'ollama serve'", file=sys.stderr)
    elif "AuthenticationError" in err_type or "401" in err_str:
        print("\n[!] Error: Authentication failed.", file=sys.stderr)
        print(
            "    Hint: Check that your API key in .env is valid and active.",
            file=sys.stderr,
        )
    elif "NotFoundError" in err_type or "model not found" in err_str.lower():
        print("\n[!] Error: Model not found.", file=sys.stderr)
        print(
            "    Hint: If using Ollama, ensure the required model is pulled (e.g. 'ollama pull qwen3:8b').",
            file=sys.stderr,
        )
    else:
        print(f"\n[!] Execution Error ({err_type}): {err_str}", file=sys.stderr)


def main() -> None:
    use_safe_console_encoding()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Multi-Agent System: Autonomous planning, execution, and review loop.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="The task description for the multi-agent system to solve.",
    )
    parser.add_argument(
        "--task-file",
        type=str,
        default=None,
        help="Path to a text file containing the task description.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use scripted Mock LLM (runs with no API keys, model, or network).",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Skip Firecrawl web research for the researcher agent (live runs only).",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Resume an existing run by its run ID.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_ROUNDS,
        help=f"Maximum review and revision rounds (default: {MAX_ROUNDS}).",
    )

    args = parser.parse_args()

    task = args.task
    if args.task_file:
        task_path = Path(args.task_file)
        if not task_path.exists():
            print(f"Error: Task file not found: {args.task_file}", file=sys.stderr)
            sys.exit(1)
        task = task_path.read_text(encoding="utf-8").strip()

    if not task and not args.resume:
        parser.print_help()
        sys.exit(1)

    llm = MockLLM() if args.mock else call_llm
    if args.mock:
        pass  # MockLLM ignores prompts, so no web research is wired in mock runs
    elif args.no_web:
        print("Web research: disabled (--no-web)")
    else:
        # Researcher steps get live Firecrawl pages appended to their prompt.
        llm = build_research_llm(llm, notify=print)
        print("Web research: Firecrawl enabled for the researcher agent (use --no-web to skip)")

    try:
        state = run_task(
            task=task,
            llm=llm,
            max_rounds=args.max_rounds,
            run_id=args.resume,
        )
    except Exception as exc:
        handle_backend_error(exc)
        sys.exit(1)

    # Save runs/<run_id>/final_output.md before printing
    runs_dir = Path(RUNS_DIR)
    final_output_file = runs_dir / state.run_id / "final_output.md"
    final_output_file.parent.mkdir(parents=True, exist_ok=True)
    full_output = state.final_output or "(No final output produced)"
    final_output_file.write_text(full_output, encoding="utf-8")

    # Print Plan (truncating long instructions if necessary)
    print("\n" + "=" * 65)
    print(f"RUN ID: {state.run_id}")
    print(f"TASK  : {state.task}")
    print("=" * 65)

    if state.plan:
        print("\n--- Plan ---")
        for idx, step in enumerate(state.plan.steps, 1):
            deps = f" (depends on: {', '.join(step.depends_on)})" if step.depends_on else ""
            instr = step.instruction if len(step.instruction) <= 120 else step.instruction[:117] + "..."
            print(f"  Step {step.id} [{step.agent}]{deps}: {instr}")

        print("\n--- Checklist Criteria ---")
        for idx, criterion in enumerate(state.plan.checklist, 1):
            crit_disp = criterion if len(criterion) <= 120 else criterion[:117] + "..."
            print(f"  {idx}. {crit_disp}")

    # Print Each Round's Verdict
    print("\n--- Review Verdicts ---")
    for r_idx, verdict in enumerate(state.verdicts, 1):
        status_label = "PASSED" if verdict.passed else "FAILED"
        summary_disp = verdict.summary if len(verdict.summary) <= 120 else verdict.summary[:117] + "..."
        print(f"  Round {r_idx}: [{status_label}] {summary_disp}")
        if verdict.issues:
            for issue in verdict.issues:
                prob = issue.problem if len(issue.problem) <= 120 else issue.problem[:117] + "..."
                fix = issue.fix if len(issue.fix) <= 120 else issue.fix[:117] + "..."
                print(f"    - Issue in Step {issue.step_id}: {prob}")
                print(f"      Suggested Fix: {fix}")

    # Print Final Result (never print long outputs in full to console)
    print("\n" + "=" * 65)
    print(f"FINAL STATUS: {state.status.upper()} (Rounds Used: {state.rounds_used})")
    print("=" * 65)
    print("\nFinal Result Output:")
    if len(full_output) > CONSOLE_MAX_CHARS:
        preview = (
            full_output[:CONSOLE_MAX_CHARS]
            + f"\n\n... [Output truncated for console display ({len(full_output)} chars total)."
            f"\n    Full deliverable saved to: {final_output_file}]"
        )
        print(preview)
    else:
        print(full_output)

    print(f"\nFinal deliverable saved to: {final_output_file}")


if __name__ == "__main__":
    main()
