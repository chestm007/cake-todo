from __future__ import annotations

import json
from pathlib import Path


class Config:
    def __init__(self) -> None:
        self.path = Path.home() / ".config" / "todo-manager" / "config.json"
        self.hierarchy: list[str] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.hierarchy = list(data.get("hierarchy", []))
        except (FileNotFoundError, json.JSONDecodeError):
            self.hierarchy = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"hierarchy": self.hierarchy}, indent=2) + "\n", encoding="utf-8")
