"""CustomTkinter desktop GUI for the Multi-Agent Kernal (Windows).

The orchestrator, agents, executor, reviewer and pipeline modules are used
exactly as they are from the command line (main.py). The GUI never rewrites
the pipeline: it runs the unmodified `pipeline.run_task` on a background
thread and wraps the *injected* llm callable to narrate every hand-off
(orchestrator -> agent -> reviewer -> revision) into the live log panel.
Progress is reported with a thread-safe queue that the Tk main loop drains,
so the window never freezes.

Run it:
    .venv\\Scripts\\python.exe gui.py        (or double-click run_gui.bat)

Build a single windowed .exe (no console) with PyInstaller:
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
        --name "AI-Agent-Kernal" --collect-all customtkinter gui.py
  (add --icon "app.ico" if you have an icon file)

Notes for the frozen exe:
  * Drop the .env file next to the .exe (or set GROQ_API_KEY / GEMINI_API_KEY
    as environment variables) for live mode; "Mock mode" needs neither.
  * Relative paths (runs/) are resolved against the working directory the
    .exe is started from, so launch it from the folder that should hold runs/.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk
from dotenv import load_dotenv

from config import MAX_ROUNDS, RUNS_DIR
from main import sanitize_message
from mock_llm import MockLLM
from pipeline import run_task
from schemas import RunState
import state as state_module

try:
    from llm import call_llm
except ImportError:  # pragma: no cover - same guard as main.py
    from Llm import call_llm

APP_TITLE = "Multi-Agent Kernal"
EVENT_POLL_MS = 100  # how often the UI thread drains queued log events
LOG_PREVIEW_CHARS = 220

# Dark-mode friendly tag colors for the live log (CTkTextbox.tag_config).
LOG_COLORS = {
    "info": "#e5e7eb",
    "time": "#6b7280",
    "handoff": "#7dd3fc",  # orchestrator handing work to an agent
    "ok": "#86efac",  # an agent finished
    "plan": "#c4b5fd",  # plan / checklist
    "review": "#fde68a",  # reviewer is checking
    "revise": "#fdba74",  # review-and-revise loop
    "pass": "#6ee7b7",  # verdict passed
    "fail": "#fca5a5",  # verdict failed
    "warn": "#fbbf24",
    "error": "#ef4444",
    "detail": "#9ca3af",
}

# The executor and reviewer build their prompts in a fixed shape
# (see executor.build_user_message / reviewer.build_review_message), which is
# what lets the GUI describe each hand-off without touching those modules.
_STEP_RE = re.compile(
    r"Current Step:\s*(?P<step_id>\S+)\s*\((?P<agent>[^)]+)\)\s*\n"
    r"Instruction:\s*\n(?P<instruction>.*?)(?=\n\n|\Z)",
    re.DOTALL,
)
_FEEDBACK_RE = re.compile(
    r"Reviewer Feedback:\s*\n-\s*Problem:\s*(?P<problem>.*?)\n"
    r"-\s*Recommended Fix:\s*(?P<fix>.*)",
    re.DOTALL,
)
REVISION_MARKER = "Reviewer Feedback:"
RETRY_MARKER = "Previous attempt produced an invalid response"


def _preview(text: Any, limit: int = LOG_PREVIEW_CHARS) -> str:
    """Collapse whitespace and shorten long text so log lines stay readable."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: max(limit - 3, 0)] + "..."


def _parse_step_prompt(user: str) -> dict[str, str] | None:
    """Read the step id/agent/instruction (and reviewer feedback) out of a step prompt."""
    match = _STEP_RE.search(user)
    if match is None:
        return None

    info = {
        "step_id": match.group("step_id"),
        "agent": match.group("agent"),
        "instruction": _preview(match.group("instruction")),
    }

    feedback = _FEEDBACK_RE.search(user)
    if feedback is not None:
        info["problem"] = _preview(feedback.group("problem"))
        info["fix"] = _preview(feedback.group("fix"))
    return info


def describe_error(exc: Exception) -> str:
    """Turn a failure into one actionable line (mirrors main.handle_backend_error)."""
    detail = sanitize_message(str(exc))
    name = type(exc).__name__

    if "environment variable first" in detail or "API_KEY" in detail:
        return (
            "ERROR: Missing API key. Put GROQ_API_KEY (or GEMINI_API_KEY) in your .env file, "
            "or tick 'Mock mode' to run offline. Details: " + detail
        )
    if (
        "11434" in detail
        or "ConnectError" in name
        or "ConnectionRefused" in detail
        or "Failed to connect" in detail
    ):
        return (
            "ERROR: Cannot reach Ollama at http://localhost:11434. Start it with 'ollama serve', "
            "or tick 'Mock mode'. Details: " + detail
        )
    if "AuthenticationError" in name or "401" in detail:
        return "ERROR: Authentication failed - check that the API key in .env is valid. Details: " + detail
    if "NotFoundError" in name or "model not found" in detail.lower():
        return (
            "ERROR: Model not found. If you use Ollama, pull the model first "
            "(e.g. 'ollama pull qwen3:8b'). Details: " + detail
        )
    return f"ERROR ({name}): {detail}"


def _as_dict(response: Any) -> dict[str, Any] | None:
    """Accept the dict a json_mode llm call returns, or a JSON string wrapping one."""
    if isinstance(response, dict):
        return response
    if isinstance(response, str):
        text = response.strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _describe_plan(response: Any) -> list[tuple[str, str]] | None:
    """Turn a planner response into PLAN READY log lines, or None if it is not a plan."""
    plan = _as_dict(response)
    if plan is None:
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return None

    raw_checklist = plan.get("checklist")
    checklist = raw_checklist if isinstance(raw_checklist, list) else []
    lines: list[tuple[str, str]] = [
        (f"PLAN READY: {len(steps)} steps, {len(checklist)} checklist criteria", "plan")
    ]

    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        depends_on = step.get("depends_on") or []
        deps = f" [needs {', '.join(str(dep) for dep in depends_on)}]" if depends_on else ""
        lines.append(
            (
                f"   {index}. {step.get('id')} [{step.get('agent')}]{deps}: "
                f"{_preview(step.get('instruction', ''), 120)}",
                "plan",
            )
        )
    for index, item in enumerate(checklist, 1):
        lines.append((f"   check {index}: {_preview(item, 120)}", "detail"))
    return lines


def _describe_verdict(round_number: int, response: Any) -> list[tuple[str, str]]:
    """Turn a reviewer response into ROUND PASSED/FAILED log lines."""
    verdict = _as_dict(response)
    if verdict is None or "passed" not in verdict:
        return []

    summary = _preview(verdict.get("summary") or "", 200)
    raw_issues = verdict.get("issues")
    issues = raw_issues if isinstance(raw_issues, list) else []

    if verdict.get("passed"):
        return [(f"ROUND {round_number}/{MAX_ROUNDS} PASSED: {summary or 'checklist satisfied'}", "pass")]

    lines: list[tuple[str, str]] = [
        (
            f"ROUND {round_number}/{MAX_ROUNDS} FAILED: {len(issues)} issue(s) - "
            f"{summary or 'checklist not satisfied'}",
            "fail",
        )
    ]
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        lines.append((f"   ! {issue.get('step_id')}: {_preview(issue.get('problem', ''), 140)}", "fail"))
        lines.append((f"     fix -> {_preview(issue.get('fix', ''), 140)}", "detail"))
    return lines


class ObservingLLM:
    """Wrap the injected llm callable so the GUI can narrate the orchestration.

    This is the only "hook" the GUI needs: pipeline.run_task and every agent
    continue to call llm(role, system, user, json_mode=False), and this wrapper
    inspects the prompt they built to report who is being handed what.
    """

    def __init__(self, llm: Callable[..., Any], emit: Callable[[str, str], None]) -> None:
        self._llm = llm
        self._emit = emit  # thread-safe: it only puts events on a queue
        self.planner_calls = 0
        self.review_calls = 0
        self._plan_shown = False
        self._step_attempts: dict[str, int] = {}

    def __call__(self, role: str, system: str, user: str, json_mode: bool = False) -> Any:
        started = time.perf_counter()

        if role == "orchestrator":
            self.planner_calls += 1
            suffix = ""
            if self.planner_calls > 1:
                suffix = f" (retry {self.planner_calls - 1} - previous plan was not valid JSON)"
            self._emit(f"HANDOFF -> orchestrator is planning the task into ordered steps{suffix}", "handoff")
            done_label = "orchestrator returned a plan"
        elif role == "reviewer":
            self.review_calls += 1
            retry = " (resubmitted after an invalid verdict)" if RETRY_MARKER in user else ""
            self._emit(
                f"HANDOFF -> reviewer is checking the deliverable against the checklist "
                f"(review round {self.review_calls}/{MAX_ROUNDS}){retry}",
                "review",
            )
            done_label = "reviewer returned a verdict"
        else:
            done_label = self._describe_step_call(role, user)

        try:
            response = self._llm(role, system, user, json_mode=json_mode)
        except Exception as exc:  # noqa: BLE001 - reported, then re-raised for the pipeline
            elapsed = time.perf_counter() - started
            self._emit(
                f"ERROR -> the {role} call failed after {elapsed:.2f}s: {sanitize_message(str(exc))}",
                "error",
            )
            raise

        elapsed = time.perf_counter() - started
        if isinstance(response, (dict, list)):
            size = len(json.dumps(response, default=str))
        else:
            size = len(str(response))
        self._emit(f"OK     {done_label} in {elapsed:.2f}s ({size} chars)", "ok")

        if role == "orchestrator":
            self._emit_plan(response)
        elif role == "reviewer":
            for line, tag in _describe_verdict(self.review_calls, response):
                self._emit(line, tag)
        else:
            self._emit(f"       output: {_preview(response, 160)}", "detail")

        return response

    def _emit_plan(self, response: Any) -> None:
        """Show the plan (steps + checklist) as soon as the orchestrator returns it."""
        if self._plan_shown:
            return
        lines = _describe_plan(response)
        if lines is None:
            return
        self._plan_shown = True
        for line, tag in lines:
            self._emit(line, tag)

    def _describe_step_call(self, role: str, user: str) -> str:
        """Emit the hand-off line for an agent step and return its 'finished' label."""
        step = _parse_step_prompt(user)
        if step is None:
            self._emit(f"HANDOFF -> {role} agent is working", "handoff")
            return f"{role} agent finished"

        step_id = step["step_id"]
        agent = step["agent"]
        attempts = self._step_attempts.get(step_id, 0) + 1
        self._step_attempts[step_id] = attempts

        if REVISION_MARKER in user:
            self._emit(
                f"HANDOFF -> {step_id} goes back to {agent} to revise (attempt {attempts})",
                "revise",
            )
            if "problem" in step:
                self._emit(f"       reviewer problem: {step['problem']}", "detail")
                self._emit(f"       reviewer fix    : {step['fix']}", "detail")
        else:
            self._emit(f"HANDOFF -> {step_id} to {agent}: {step['instruction']}", "handoff")

        return f"{agent} finished {step_id}"


def _format_summary(state: RunState) -> list[tuple[str, str]]:
    """Closing log lines for a finished run: plan, attempts, verdicts, final status."""
    lines: list[tuple[str, str]] = [("RUN SUMMARY", "plan")]

    if state.plan:
        lines.append((f"Plan: {len(state.plan.steps)} steps, {len(state.plan.checklist)} checklist criteria", "plan"))
        for step in state.plan.steps:
            result = state.results.get(step.id)
            attempts = result.attempts if result else 0
            lines.append((f"   {step.id} [{step.agent}] attempts={attempts}", "detail"))

    for index, verdict in enumerate(state.verdicts, 1):
        if verdict.passed:
            lines.append((f"Round {index}: PASSED - {_preview(verdict.summary, 200)}", "pass"))
        else:
            lines.append(
                (f"Round {index}: FAILED - {len(verdict.issues)} issue(s) - {_preview(verdict.summary, 200)}", "fail")
            )
            for issue in verdict.issues:
                lines.append((f"   {issue.step_id}: {_preview(issue.problem, 160)}", "fail"))
                lines.append((f"      fix -> {_preview(issue.fix, 160)}", "detail"))

    status_tag = "pass" if state.status == "passed" else "warn"
    lines.append((f"FINAL STATUS: {state.status.upper()} (rounds used: {state.rounds_used})", status_tag))
    return lines


def _write_final_output(state: RunState, output: str) -> str:
    """Save runs/<run_id>/final_output.md exactly like main.py does (best effort)."""
    try:
        path = Path(RUNS_DIR) / state.run_id / "final_output.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        return str(path)
    except OSError as exc:  # a read-only folder must not hide the deliverable
        return f"(could not write final_output.md: {exc})"


class KernalGUI(ctk.CTk):
    """Task box + Mock mode + Run button + live orchestration log + final output."""

    def __init__(self) -> None:
        super().__init__()

        self._events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._running = False
        self._closing = False
        self._run_id: str | None = None
        self._worker: threading.Thread | None = None

        self.title(f"{APP_TITLE} - orchestrator, agents, reviewer")
        self.geometry("1180x780")
        self.minsize(940, 620)

        self._body_font = ctk.CTkFont(family="Segoe UI", size=13)
        self._mono_font = ctk.CTkFont(family="Consolas", size=12)

        self._build_widgets()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_events()

    # ------------------------------------------------------------------ UI --

    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=APP_TITLE,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text=(
                "The orchestrator splits the task into steps and hands each one to an agent, "
                "the reviewer checks the result against the checklist, and faulty steps are "
                f"revised for up to {MAX_ROUNDS} rounds."
            ),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#9ca3af",
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        task_frame = ctk.CTkFrame(self, corner_radius=10)
        task_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 6))
        task_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            task_frame,
            text="Task",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        self.task_box = ctk.CTkTextbox(task_frame, height=86, wrap="word", font=self._body_font)
        self.task_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self.task_box.insert(
            "1.0",
            "Research how capacitors work and write a one-page summary for first-year students",
        )

        controls = ctk.CTkFrame(task_frame, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        controls.grid_columnconfigure(3, weight=1)

        self.mock_var = ctk.BooleanVar(value=True)
        self.mock_check = ctk.CTkCheckBox(
            controls,
            text="Mock mode",
            variable=self.mock_var,
            font=self._body_font,
            checkbox_width=20,
            checkbox_height=20,
            command=self._on_mock_toggle,
        )
        self.mock_check.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ctk.CTkLabel(
            controls,
            text="(no API keys, no network)",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#9ca3af",
        ).grid(row=0, column=1, sticky="w", padx=(0, 18))

        self.run_button = ctk.CTkButton(
            controls,
            text="Run",
            width=140,
            height=34,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            command=self.on_run,
        )
        self.run_button.grid(row=0, column=2, sticky="w")

        self.status_label = ctk.CTkLabel(
            controls,
            text="Idle",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#9ca3af",
            anchor="e",
        )
        self.status_label.grid(row=0, column=3, sticky="e", padx=(12, 0))

        panels = ctk.CTkFrame(self, fg_color="transparent")
        panels.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 4))
        panels.grid_rowconfigure(0, weight=1)
        panels.grid_columnconfigure(0, weight=3, uniform="panels")
        panels.grid_columnconfigure(1, weight=2, uniform="panels")

        log_frame = ctk.CTkFrame(panels, corner_radius=10)
        log_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            log_frame,
            text="Live activity log",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.log_box = ctk.CTkTextbox(log_frame, wrap="word", font=self._mono_font)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

        out_frame = ctk.CTkFrame(panels, corner_radius=10)
        out_frame.grid(row=0, column=1, sticky="nsew")
        out_frame.grid_columnconfigure(0, weight=1)
        out_frame.grid_rowconfigure(1, weight=1)
        out_header = ctk.CTkFrame(out_frame, fg_color="transparent")
        out_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        out_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            out_header,
            text="Final output",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.copy_button = ctk.CTkButton(
            out_header,
            text="Copy",
            width=84,
            height=28,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self.on_copy,
        )
        self.copy_button.grid(row=0, column=1, sticky="e")
        self.output_box = ctk.CTkTextbox(out_frame, wrap="word", font=self._body_font)
        self.output_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.output_box.configure(state="disabled")

        self.footer_label = ctk.CTkLabel(
            self,
            text=f"Run ID: -    |    state, log and final_output.md are written to {RUNS_DIR}\\<run_id>\\",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#6b7280",
            anchor="w",
        )
        self.footer_label.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 10))

        self._configure_log_tags()

    def _configure_log_tags(self) -> None:
        """Color-code log lines. CTkTextbox forwards tag_* calls to its tkinter.Text."""
        for tag, color in LOG_COLORS.items():
            try:
                self.log_box.tag_config(tag, foreground=color)
            except Exception:  # pragma: no cover - colors are cosmetic only
                pass

    # ------------------------------------------------------- log plumbing --

    def _emit_event(self, message: str, tag: str = "info") -> None:
        """Queue a log line. Called from the worker thread and from the UI thread."""
        self._events.put(("log", message, tag))

    def _append_log(self, message: str, tag: str = "info") -> None:
        """Append one timestamped, colour-coded line to the live log (UI thread only)."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] ", "time")
        self.log_box.insert("end", f"{message}\n", tag if tag in LOG_COLORS else "info")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_output(self, text: str) -> None:
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", "end")
        if text:
            self.output_box.insert("1.0", text)
        self.output_box.configure(state="disabled")

    def _drain_events(self) -> None:
        """Render everything the worker thread queued, then reschedule itself."""
        if self._closing:
            return
        try:
            while True:
                kind, message, tag = self._events.get_nowait()
                if kind == "log":
                    self._append_log(message, tag)
                elif kind == "output":
                    self._set_output(message)
                elif kind == "done":
                    self._finish_run(message)
        except queue.Empty:
            pass
        self.after(EVENT_POLL_MS, self._drain_events)

    def _on_mock_toggle(self) -> None:
        if not self._running:
            mode = "Mock mode ON - runs offline with the scripted MockLLM" if self.mock_var.get() else "Live mode - real models will be called"
            self.status_label.configure(text=mode)

    # ------------------------------------------------------------ running --

    def on_run(self) -> None:
        """Start a run on a background thread so the window stays responsive."""
        if self._running:
            return

        task = self.task_box.get("1.0", "end").strip()
        if not task:
            self._append_log("Type a task before pressing Run.", "warn")
            self.status_label.configure(text="No task entered")
            return

        mock_mode = bool(self.mock_var.get())
        mode_text = (
            "mock mode - scripted MockLLM, no API keys or network needed"
            if mock_mode
            else "live mode - calling the models configured in llm.py"
        )

        self._running = True
        self._run_id = state_module.new_run_id()
        self._set_output("")
        self.run_button.configure(state="disabled", text="Running...")
        self.status_label.configure(text="Running...")
        self.footer_label.configure(
            text=f"Run ID: {self._run_id}    |    writing to {RUNS_DIR}\\{self._run_id}\\"
        )

        self._append_log("-" * 96, "detail")
        self._append_log(f"RUN {self._run_id} started ({mode_text})", "plan")
        self._append_log(f"TASK: {_preview(task, 300)}", "detail")

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(task, mock_mode, self._run_id),
            name=f"kernal-run-{self._run_id}",
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, task: str, mock_mode: bool, run_id: str) -> None:
        """Background thread: run the untouched pipeline, report via the event queue."""
        try:
            base_llm = MockLLM() if mock_mode else call_llm
            observed_llm = ObservingLLM(base_llm, self._emit_event)
            # A fresh run id is created here (state.new_run_id) and handed to the
            # pipeline, which treats an unknown id as a brand new run. That way the
            # GUI knows runs/<run_id>/ up front and can point the user at it.
            state = run_task(task=task, llm=observed_llm, max_rounds=MAX_ROUNDS, run_id=run_id)
        except Exception as exc:
            self._emit_event(describe_error(exc), "error")
            self._emit_event("Run aborted. Fix the problem above and press Run again.", "warn")
            self._events.put(("done", "Failed - see log", ""))
            return

        output = state.final_output or "(No final output was produced)"
        saved = _write_final_output(state, output)

        self._emit_event("", "info")
        for line, tag in _format_summary(state):
            self._emit_event(line, tag)
        self._emit_event(f"Deliverable saved to {saved}", "detail")

        self._events.put(("output", output, ""))
        if state.status == "passed":
            self._events.put(("done", f"Finished - passed in {state.rounds_used} round(s)", ""))
        else:
            self._events.put(("done", f"Finished - {state.status} (open issues remain)", ""))

    def _finish_run(self, status_text: str = "Idle") -> None:
        self._running = False
        self.run_button.configure(state="normal", text="Run")
        self.status_label.configure(text=status_text)

    def on_copy(self) -> None:
        """Copy the final output panel contents to the Windows clipboard."""
        text = self.output_box.get("1.0", "end").strip()
        if not text:
            self.status_label.configure(text="Nothing to copy yet")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()  # keeps the clipboard populated after Copy returns
        self.status_label.configure(text=f"Copied {len(text)} characters")
        self._append_log(f"Copied the final output ({len(text)} chars) to the clipboard.", "detail")

    def _on_close(self) -> None:
        if self._running:
            stay_open = messagebox.askyesno(
                APP_TITLE,
                "A run is still in progress.\n\nQuit anyway? Everything that already finished stays "
                "in runs\\<run_id>\\state.json, so you can resume it with main.py --resume.",
            )
            if not stay_open:
                return
        self._closing = True
        self.destroy()


def main() -> None:
    load_dotenv()  # same as main.py: keys come from the environment, never from code
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    KernalGUI().mainloop()


if __name__ == "__main__":
    main()
