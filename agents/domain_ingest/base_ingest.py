"""Abstract ingest interface."""

from __future__ import annotations

import json
import logging
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("domain_ingest.base")


class BaseIngest(ABC):
    source_name: str = "base"

    @abstractmethod
    def fetch(self) -> Any:
        ...

    @abstractmethod
    def parse(self, raw: Any) -> list[dict[str, Any]]:
        ...

    def validate(self, record: dict[str, Any]) -> bool:
        required = {"source", "ingested_at", "signal_type", "value", "confidence", "valid_until"}
        if not required.issubset(set(record.keys())):
            return False
        return "ticker" in record

    def save(self, records: list[dict[str, Any]], *, day_stamp: str, root: Path) -> Path | None:
        out_dir = root / "data" / "domain_intelligence" / self.source_name
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{day_stamp}.json"
        try:
            path.write_text(json.dumps({"records": records}, indent=2, default=str), encoding="utf-8")
            return path
        except Exception:
            logger.exception("%s save failed", self.source_name)
            return None
