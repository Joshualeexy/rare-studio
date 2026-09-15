"""
State Machine & Checkpointing System for Video Engine.
Modeled after AffiliateKage's resilient pipeline_state architecture.
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


class CheckpointManager:
    """
    Manages persistent state across all video pipeline stages.
    Guarantees atomic writes so power loss, OOM, or Ctrl+C never corrupts state.
    """

    def __init__(self, state_file: str = "pipeline_state.json"):
        self.state_path = Path(state_file).resolve()

    def exists(self) -> bool:
        return self.state_path.exists()

    def load(self) -> Optional[Dict[str, Any]]:
        """Load current state or return None if not present or corrupt."""
        if not self.state_path.exists():
            return None
        try:
            content = self.state_path.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception as e:
            print(f"[Checkpoint] Warning: Failed to parse {self.state_path}: {e}")
            return None

    def save(self, state: Dict[str, Any]) -> None:
        """Atomically persist state to disk."""
        state["_updated_at"] = time.time()
        parent_dir = self.state_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        # Atomic write pattern: write to tmp file in same dir, then replace
        temp_file = parent_dir / f".{self.state_path.name}.tmp.{os.getpid()}"
        try:
            temp_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_file.replace(self.state_path)
        except Exception as e:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise IOError(f"Failed to atomically save checkpoint: {e}") from e

    def clear(self) -> None:
        """Safely delete the active checkpoint file."""
        self.state_path.unlink(missing_ok=True)

    def archive(self, destination_dir: str, task_id: str) -> None:
        """Move the completed state snapshot to the task archive for historical reference."""
        dest = Path(destination_dir) / task_id / "completed_state.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            shutil.copy2(self.state_path, dest)
            self.clear()
