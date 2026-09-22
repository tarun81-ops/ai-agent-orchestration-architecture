from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Union

from config import RUNS_DIR as CONFIG_RUNS_DIR
from schemas import RunState

RUNS_DIR = Path(CONFIG_RUNS_DIR)


def new_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{timestamp}_{suffix}"


def save_state(state: RunState, runs_dir: Union[Path, str] | None = None) -> Path:
    target_dir = Path(runs_dir) if runs_dir is not None else Path(RUNS_DIR)
    base = target_dir / state.run_id
    base.mkdir(parents=True, exist_ok=True)
    state_file = base / "state.json"
    state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return state_file


def load_state(run_id: str, runs_dir: Union[Path, str] | None = None) -> RunState:
    target_dir = Path(runs_dir) if runs_dir is not None else Path(RUNS_DIR)
    state_file = target_dir / run_id / "state.json"
    if not state_file.exists():
        raise FileNotFoundError(f"State file not found at: {state_file}")
    return RunState.model_validate_json(state_file.read_text(encoding="utf-8"))


def log(state: Union[RunState, str], message: str, runs_dir: Union[Path, str] | None = None) -> Path:
    run_id = state.run_id if hasattr(state, "run_id") else str(state)
    target_dir = Path(runs_dir) if runs_dir is not None else Path(RUNS_DIR)
    base = target_dir / run_id
    base.mkdir(parents=True, exist_ok=True)
    log_file = base / "log.txt"
    line = message if message.endswith("\n") else f"{message}\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line)
    return log_file
