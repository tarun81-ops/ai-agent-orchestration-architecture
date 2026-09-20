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
