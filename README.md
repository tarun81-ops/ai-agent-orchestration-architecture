# Multi-Agent Kernal (Google ADK)

This project started as a hand-written multi-agent pipeline (v1) and is now rebuilt on
[Google's Agent Development Kit (ADK)](https://google.github.io/adk-docs/) for Python.

A central **planner** breaks your task into a plan plus a checklist, three specialist
agents then run in order (**researcher -> writer -> tester**), and finally a
**review-and-revise loop** checks the result against the checklist and fixes it, for up to
3 rounds. As soon as the work passes, the reviewer stops the loop early.

Everything from v1 is still here, untouched, in **`legacy_v1\`** (CLI, GUI, tests, docs).

---

## 1. Folders and files

| Path | What it is |
| --- | --- |
| `kernel_agent\` | **The new ADK agent package.** `agent.py` defines `root_agent` |
| `kernel_agent\agent.py` | Builds the agent tree (planner, specialists, loop); picks the model |
| `kernel_agent\prompts.py` | Your v1 prompts, reused (only the output contracts changed) |
| `kernel_agent\tools.py` | Firecrawl web tools (`search_web`, `scrape_page`) for the researcher |
| `kernel_agent\mock_agents.py` | Fake agents used when `KERNEL_MOCK=1` (offline demo) |
| `.env` | Your real keys. **Git-ignored - edit this file.** |
| `.env.example` | The template to copy from |
| `run_kernel.bat` | Starts the web UI and opens it in your browser |
| `pytest.ini` | Tells pytest to run only the v1 tests in `tests\` |
| `legacy_v1\` | Your v1 project exactly as it was (CLI, GUI, tests, README, ARCHITECTURE) |
| `requirements.txt` | ADK pinned to 2.9.2, plus the legacy packages |

---

## 2. Set your API key (once)

1. Get a **free** Google AI Studio key: <https://aistudio.google.com/apikey>
2. Open the `.env` file in this folder with Notepad and replace the placeholder line:

   ```
   GOOGLE_API_KEY=your_key_here        <- paste your key after the "="
   KERNEL_MODEL=gemini-3.6-flash       <- optional: any current AI Studio model
   ```

3. Save the file. No quotes, no spaces around the `=`.

`kernel_agent\.env` is a copy of the same file, and **ADK's CLI loads that copy when it
starts the agent** (a later `.env` load wins, so the copy matters). `run_kernel.bat` copies
`.env` across on every start, so if you edit the key just re-run the launcher - or copy it
yourself: `copy .env kernel_agent\.env`. If the copy still has the placeholder, the agent
prints a warning telling you exactly this.
If the key is missing or wrong, ADK reports `API_KEY_INVALID` - that is the signal to
redo step 2. (No key at all? You can still run everything in mock mode below.)

---

## 3. Run it

### A. Normal mode (real Gemini models)

```powershell
# one-shot: run a single task and exit
.venv\Scripts\adk.exe run kernel_agent "Explain in two sentences how a capacitor stores charge"

# interactive chat in the terminal
.venv\Scripts\adk.exe run kernel_agent

# browser UI - the easiest way to watch the agents work
run_kernel.bat
```

`run_kernel.bat` starts the ADK web UI and opens <http://localhost:8000> for you. On the
page: pick **kernel_agent** in the top-left dropdown, type a task, press Enter, and watch
each agent's output appear in order. Press `Ctrl+C` in the black window to stop the server.

### B. Mock mode (no API key, no network, no cost)

Mock mode swaps in fake agents that return scripted text, so the full flow still runs -
including the round-1 failure and the round-2 pass, exactly like v1's `MockLLM`.

```powershell
# in cmd / PowerShell, for that window only:
set KERNEL_MOCK=1
.venv\Scripts\adk.exe run kernel_agent "any task - the answer is canned"

# or start the web UI in mock mode:
set KERNEL_MOCK=1
run_kernel.bat
```

---

## 4. How the agent flow works

```
                     ┌──────────────── your task ────────────────┐
                     ▼                                           │
  planner  ──►  researcher  ──►  writer  ──►  tester  ──►  refine_loop
 (plan +         (facts,        (draft)     (verified      (up to 3 rounds)
  checklist)      Firecrawl)                 result)        │
                                                             ├─ reviewer: all checklist
                                                             │    items ok? -> exit_loop
                                                             │    not ok?   -> write feedback
                                                             └─ refiner: apply the feedback
                                                                          to the result
```

ADK runs exactly that order: one `SequentialAgent` (named `kernel_agent`) contains the
planner, the three specialists and the loop. The loop is a `LoopAgent` with
`max_iterations=3`, so at worst it revisits the result three times.

**Agents never talk to each other directly.** They share one session state, and each
instruction asks for the pieces it needs with `{state_key}` templating:

| Agent | Writes | Reads |
| --- | --- | --- |
| `planner` | `{plan}` (plan + checklist) | your task (the conversation) |
| `researcher` | `{research}` | `{plan}` |
| `writer` | `{draft}` | `{plan}`, `{research}` |
| `tester` | `{result}` (final deliverable) | `{plan}`, `{research}`, `{draft}` |
| `reviewer` | `{review}` (feedback) | `{plan}`, `{result}` |
| `refiner` | `{result}` (revised) | `{plan}`, `{result}`, `{review}` |

The reviewer sits *before* the refiner inside the loop so the round-1 review looks at the
tester's result, and the refiner only ever runs once there is feedback to apply.

This is what replaces v1's `executor.py` and `state.py`: the old code copied the outputs
named in a step's `depends_on` and saved `runs\<id>\state.json`; ADK keeps that state in
the session and shows every event in the web UI instead.

---

## 5. Notes for a v1 user

* **What changed:** the plan is plain text now (v1 used JSON), and the reviewer either
  calls the built-in `exit_loop` tool (pass) or writes feedback (fail) instead of returning
  a JSON verdict - that is how an ADK `LoopAgent` stops.
* **What stayed:** your agent personas, rules and wording, the researcher's Firecrawl web
  access, the 3-round limit, and the "plan first, verify against a checklist" approach.
* **What is gone:** the per-step `depends_on` graph (the specialists now run in a fixed
  order) and the "re-run only the faulty steps" behaviour (the loop now revises the final
  result). The CustomTkinter GUI is retired - `adk web` replaces it.
* **Deprecation warning:** `SequentialAgent` and `LoopAgent` are marked *deprecated* in
  ADK 2.x (Google is moving to a `Workflow` graph API). They still work and are the
  simplest way to express this pipeline; `requirements.txt` pins `google-adk==2.9.2` so a
  future release cannot break the project overnight. `agent.py` silences just those two
  warnings, with a comment explaining why.
* **v1 is still runnable:** the old code and its 69 pytest tests stay in place
  (`legacy_v1\` holds the frozen copy). Run them from the project root:

  ```powershell
  .venv\Scripts\python.exe -m pytest -q
  ```

---

## 6. Desktop shortcut (optional)

**Easiest way:** right-click `run_kernel.bat` -> *Show more options* -> *Send to* ->
*Desktop (create shortcut)*. Then right-click the new shortcut -> *Properties* and set
*Start in* to the project folder, and *Change Icon* to `app.ico`.

**Or paste this into PowerShell** to create it in one go:

```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Multi-Agent Kernal.lnk")
$lnk.TargetPath       = "C:\Users\tarun verma\ai agent kernal\run_kernel.bat"
$lnk.WorkingDirectory = "C:\Users\tarun verma\ai agent kernal"
$lnk.IconLocation     = "C:\Users\tarun verma\ai agent kernal\app.ico"
$lnk.Description      = "Start the ADK web UI for the Multi-Agent Kernal"
$lnk.Save()
```
