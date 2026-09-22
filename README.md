# Multi-Agent Kernal (Google ADK)

A central **planner** turns your task into a plain-text plan and a checklist, three
specialist agents then run in order (**researcher → writer → tester**), and a
**review-and-revise loop** checks the result against the checklist and fixes it for up
to 3 rounds. The loop stops the moment the reviewer is satisfied.

Built on [Google's Agent Development Kit (ADK)](https://google.github.io/adk-docs/)
for Python. The researcher can read live web pages via Firecrawl.

---

## Files

| Path | What it is |
| --- | --- |
| `kernel_agent/` | The ADK agent package. `agent.py` builds the agent tree. |
| `kernel_agent/agent.py` | Builds the agent tree; picks the model; handles mock mode. |
| `kernel_agent/prompts.py` | Agent personas, rules and wording. |
| `kernel_agent/tools.py` | Firecrawl web tools (`search_web`, `scrape_page`) for the researcher. |
| `kernel_agent/mock_agents.py` | Fake agents used when `KERNEL_MOCK=1` (offline demo, no API key). |
| `kernel_agent/firecrawl_client.py` | Firecrawl v2 HTTP client (stdlib only), used by `tools.py`. |
| `.env` | Your API keys. **Git-ignored — edit this file.** |
| `run_kernel.bat` | Starts the ADK web UI on http://localhost:8000 and opens it in your browser. |
| `requirements.txt` | ADK pinned to 2.9.2 + python-dotenv. |
| `app.ico` | Icon used if you build a desktop shortcut. |

---

## Set your API key (once)

1. Get a **free** Google AI Studio key: <https://aistudio.google.com/apikey>
2. Open `.env` in this folder with Notepad and add a line like:

   ```
   GOOGLE_API_KEY=AIza...
   ```

   No quotes, no spaces around the `=`. Save the file.

`kernel_agent/.env` is a copy of the same file. `run_kernel.bat` copies `.env` across on
every start, so if you edit the key just re-run the launcher — or copy it yourself:
`copy .env kernel_agent\.env`.

If the key is missing or wrong, ADK reports `API_KEY_INVALID` — that is the signal to redo
step 2. No key at all? Run in mock mode instead (below).

---

## Run it

### Normal mode (real Gemini models)

```powershell
# one-shot: run a single task and exit
.venv\Scripts\adk.exe run kernel_agent "Explain in two sentences how a capacitor stores charge"

# interactive chat in the terminal
.venv\Scripts\adk.exe run kernel_agent

# browser UI — the easiest way to watch the agents work
run_kernel.bat
```

`run_kernel.bat` starts the ADK web UI on <http://localhost:8000>. On the page: pick
**kernel_agent** in the top-left dropdown, type a task, press Enter, and watch each agent's
output appear in order. Press `Ctrl+C` in the black window to stop the server.

### Mock mode (no API key, no network, no cost)

Mock mode swaps in fake agents that return scripted text, so the full flow still runs —
including the round-1 failure and the round-2 pass.

```powershell
# in cmd / PowerShell, for that window only:
set KERNEL_MOCK=1
.venv\Scripts\adk.exe run kernel_agent "any task - the answer is canned"

# or start the web UI in mock mode:
set KERNEL_MOCK=1
run_kernel.bat
```

---

## How the agent flow works

```
                     ┌──────────────── your task ────────────────┐
                     ▼                                           │
  planner  ──►  researcher  ──►  writer  ──►  tester  ──►  refine_loop
 (plan +         (facts,        (draft)     (verified      (up to 3 rounds)
  checklist)      Firecrawl)                 result)        │
                                                             ├─ reviewer: all checklist
                                                             │    items ok? → exit_loop
                                                             │    not ok?   → write feedback
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

The reviewer sits *before* the refiner inside the loop, so round 1 reviews the tester's
result and the refiner only ever runs once there is feedback to apply.

---

## Optional Firecrawl web research

The researcher can read live web pages. The key is `FIRECRAWL_API_KEY=fc-...` in `.env`.

- `search_web(query)` — search the live web, return the top pages with their text.
- `scrape_page(url)` — fetch one URL as markdown.

If the key is missing or Firecrawl fails, the researcher keeps working with the plain
prompt — nothing else in the pipeline is affected.

---

## Notes

- **Model**: every agent uses `gemini-3.6-flash` by default. Override in `.env` with
  `KERNEL_MODEL=<any AI Studio model>`.
- **Retries**: free API keys regularly hit 429/503. ADK retries each agent with growing
  waits (4 attempts, 2s → 30s).
- **Deprecation**: `SequentialAgent` and `LoopAgent` are marked deprecated in ADK 2.x
  (Google is moving to a `Workflow` graph API). They still work and are the simplest way to
  express this pipeline; `requirements.txt` pins `google-adk==2.9.2` so a future release
  cannot break the project overnight. `agent.py` silences just those two warnings.
- **Mock agents are not the same code path as the real ones**. The fake reviewer stops the
  loop by escalating (the same effect as the real reviewer's `exit_loop` tool). If ADK's
  internal event model changes, the mock reviewer may need a small update.

---

## Desktop shortcut (optional)

**Easiest way:** right-click `run_kernel.bat` → *Show more options* → *Send to* → *Desktop
(create shortcut)*. Then right-click the new shortcut → *Properties*, set *Start in* to this
folder, and *Change Icon* to `app.ico`.

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
