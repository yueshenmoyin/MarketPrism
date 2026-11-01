from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from aitrade.dataflows.china_stock import ChinaStockError, normalize_cn_stock_symbol
from aitrade.server.a_share_collector import AShareCollector
from aitrade.server.cache_store import CacheStore


def _cache_dir() -> Path:
    configured = os.environ.get("AITRADE_DATA_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".aitrade" / "server_data_cache"


def _watchlist_file() -> Path:
    configured = os.environ.get("AITRADE_PRECACHE_WATCHLIST_FILE")
    if configured:
        return Path(configured).expanduser()
    return _cache_dir() / "watchlist.json"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _normalize_symbol_safely(symbol: str) -> str | None:
    try:
        return normalize_cn_stock_symbol(symbol)
    except ChinaStockError:
        return None


def _load_watchlist() -> list[str]:
    path = _watchlist_file()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_symbols = payload.get("symbols", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_symbols, list):
        return []
    symbols = {_normalize_symbol_safely(str(symbol)) for symbol in raw_symbols}
    return sorted(symbol for symbol in symbols if symbol)


def _save_watchlist(symbols: list[str]) -> None:
    path = _watchlist_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbols": sorted(set(symbols))}, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_precache_symbol(symbol: str) -> bool:
    normalized = _normalize_symbol_safely(symbol)
    if not normalized:
        return False
    symbols = set(_load_watchlist())
    if normalized in symbols:
        return True
    symbols.add(normalized)
    _save_watchlist(sorted(symbols))
    return True


def load_precache_symbols() -> list[str]:
    configured = os.environ.get("AITRADE_PRECACHE_SYMBOLS", "")
    env_symbols = {_normalize_symbol_safely(part.strip()) for part in configured.split(",") if part.strip()}
    watchlist_symbols = set(_load_watchlist())
    return sorted(symbol for symbol in env_symbols.union(watchlist_symbols) if symbol)


def run_precache_once(
    *,
    cache_store: CacheStore | None = None,
    collector_factory: Callable[[CacheStore], Any] = AShareCollector,
    today: date | None = None,
) -> dict[str, int]:
    symbols = load_precache_symbols()
    if not symbols:
        return {"total": 0, "complete": 0, "missing": 0}

    end = today or date.today()
    start = end - timedelta(days=_env_int("AITRADE_PRECACHE_LOOKBACK_DAYS", 120, 1))
    collector = collector_factory(cache_store or CacheStore(_cache_dir()))
    result = {"total": len(symbols), "complete": 0, "missing": 0}
    for symbol in symbols:
        try:
            payload = collector.collect_prices(symbol, start.isoformat(), end.isoformat())
        except Exception:
            payload = {"data_status": "missing"}
        if payload.get("data_status") == "complete":
            result["complete"] += 1
        else:
            result["missing"] += 1
    return result


_scheduler_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None


def _scheduler_loop() -> None:
    while True:
        try:
            run_precache_once()
        except Exception:
            pass
        time.sleep(_env_int("AITRADE_PRECACHE_INTERVAL_SECONDS", 86400, 60))


def start_precache_scheduler() -> bool:
    if not _env_bool("AITRADE_PRECACHE_ENABLED", default=False):
        return False
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            return True
        _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="aitrade-precache")
        _scheduler_thread.start()
    return True
