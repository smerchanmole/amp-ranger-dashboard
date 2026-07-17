"""Bitácora JSONL append-only para trazabilidad de preguntas y respuestas."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlAuditLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-min(limit, 500):]
        return [json.loads(line) for line in reversed(lines) if line.strip()]
