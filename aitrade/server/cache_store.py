from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


class CacheStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def path_for(self, namespace: str, parts: list[str]) -> Path:
        safe_namespace = self._safe_part(namespace)
        safe_parts = [self._safe_part(part) for part in parts]
        return self.root.joinpath(safe_namespace, *safe_parts).with_suffix(".json")

    def read(self, namespace: str, parts: list[str], ttl_seconds: int) -> dict[str, Any] | None:
        path = self.path_for(namespace, parts)
        if not path.exists():
            return None
        if ttl_seconds > 0 and time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        try:
            with path.open("r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def write(self, namespace: str, parts: list[str], payload: dict[str, Any]) -> Path:
        path = self.path_for(namespace, parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as cache_file:
            json.dump(payload, cache_file, ensure_ascii=False, indent=2)
        temp_path.replace(path)
        return path

    def _safe_part(self, value: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", str(value).strip()).strip("._")
        if cleaned:
            return cleaned[:80]
        return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]
