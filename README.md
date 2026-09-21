# Multi-Agent System (Kernal)

An autonomous multi-agent orchestration system running on Windows (PowerShell) with Python 3.10+. A central orchestrator creates an ordered, dependent plan with verification criteria. Specialized agents execute steps in sequence, passing only relevant context forward. A reviewer evaluates the final result against the checklist; on failure, faulty steps and their dependencies are re-executed for up to 3 rounds.

---

## 1. Prerequisites & Setup (Windows PowerShell)

Open PowerShell and navigate to the project directory:

```powershell
cd "C:\Users\tarun verma\ai agent kernal"
```

### Step 1: Create and activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 2: Install dependencies

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

### Step 3: Install & configure Ollama

1. Download and install Ollama for Windows from [ollama.com](https://ollama.com).
2. Pull the default local model in PowerShell:
   ```powershell
   ollama pull qwen3:8b
   ```
3. Set the context length to 8192 using `setx` so local models have adequate memory:
   ```powershell
   setx OLLAMA_CONTEXT_LENGTH 8192
   ```
4. Restart the Ollama service or application from the Windows system tray so the new environment variable takes effect.

### Step 4: Configure Groq API Key

For cloud models (orchestrator and reviewer), set your `GROQ_API_KEY`:

- **For the current PowerShell session only:**
  ```powershell
  $env:GROQ_API_KEY = "gsk_your_groq_api_key_here"
  ```
- **Persistently across sessions:**
  ```powershell
  setx GROQ_API_KEY "gsk_your_groq_api_key_here"
  ```
*(You can also place `GROQ_API_KEY=gsk_...` in a `.env` file in the project root).*

---

## 2. Connection Test

Verify backend connectivity to local Ollama and the cloud model:

```powershell
.venv\Scripts\python llm.py
```

Expected output:
- Concise test response from the `researcher` role via local Ollama.
- JSON response from the `orchestrator` role via Groq.

---

## 3. Running the System

### A. Run in Mock Mode (No API keys or network required)

Test the complete planning, execution, round 1 failure, revision, and round 2 pass loop offline:

```powershell
.venv\Scripts\python main.py --mock "demo task"
```

### B. Run a Real Task

Run a full autonomous task using real local and cloud models:

```powershell
.venv\Scripts\python main.py "Research how capacitors work and write a one-page summary for first-year students"
```

You can also pass task files:
```powershell
.venv\Scripts\python main.py --task-file task.txt
```

---

## 4. Understanding the `runs/` Folder

Every run creates a timestamped subdirectory inside `runs/<run_id>/`:

```
runs/
└── 20260920_153026_ea7a12/
    ├── state.json        # Full serialized RunState (plan, step results, verdicts, attempts)
    ├── log.txt           # Detailed timeline and per-call metrics (role, elapsed time, output size)
    └── final_output.md   # Final deliverable produced by the pipeline
```

- **`state.json`**: Saved automatically after every individual step and review round. Ensures complete state persistence and auditability.
- **`log.txt`**: Logs every stage transition, step execution, per-call durations in seconds, and output byte lengths.
- **`final_output.md`**: Clean markdown export of the final approved deliverable.

---

## 5. Resuming Interrupted Runs (`--resume`)

If a run is interrupted or halted due to network or rate limit issues, resume it using its `run_id`:

```powershell
.venv\Scripts\python main.py --resume 20260920_153026_ea7a12
```

When resuming:
1. Steps that already have saved results are automatically skipped.
2. Execution picks up from the first incomplete step.
3. The review loop continues seamlessly.

---

## 6. Running Tests

Run the test suite with pytest:

```powershell
.venv\Scripts\python -m pytest -q
```

---

## 7. Desktop GUI (Windows)

`gui.py` is a CustomTkinter front-end for the same pipeline `main.py` runs. The orchestrator, agents, executor, reviewer and pipeline modules are used unchanged: the GUI runs `pipeline.run_task` on a background thread and wraps the injected `llm` callable so it can narrate every hand-off. It provides a task box, a **Mock mode** checkbox, a **Run** button, a live activity log (orchestrator -> agent -> reviewer -> revision) and a final output panel with a **Copy** button.

### Launch it

Double-click `run_gui.bat`, or:

```powershell
.venv\Scripts\python.exe gui.py
```

- **Mock mode** (ticked by default) runs the scripted `MockLLM` offline: no API keys, no network, no Ollama.
- Untick it for a real run: the GUI uses the same `.env` keys and the same `llm.py` `ROLES` as `main.py`.
- Every run still writes `runs/<run_id>/state.json`, `log.txt` and `final_output.md`.

### Build a single .exe (no console window)

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "AI-Agent-Kernal" --collect-all customtkinter gui.py
```

The result is `dist\AI-Agent-Kernal.exe`:

- `--windowed` drops the console window (the GUI is the only window shown).
- `--onefile` packs everything into one executable.
- `--collect-all customtkinter` bundles the customtkinter theme assets so the frozen app can find them.
- Add `--icon "app.ico"` if you want a custom icon.

Copy the `.exe` next to `.env` and start it from that folder: live mode then finds the keys in the working directory, and `runs/` is created where the `.exe` was launched.

