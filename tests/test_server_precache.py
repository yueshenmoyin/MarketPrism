from datetime import date

import pytest


@pytest.mark.unit
def test_precache_watchlist_normalizes_and_deduplicates_symbols(monkeypatch, tmp_path):
    from aitrade.server import precache

    monkeypatch.setenv("AITRADE_PRECACHE_WATCHLIST_FILE", str(tmp_path / "watchlist.json"))

    precache.remember_precache_symbol("600519")
    precache.remember_precache_symbol("600519.SH")
    precache.remember_precache_symbol("000001.SZ")

    assert precache.load_precache_symbols() == ["000001.SZ", "600519.SH"]


@pytest.mark.unit
def test_precache_run_uses_env_and_watchlist_symbols(monkeypatch, tmp_path):
    from aitrade.server import precache
    from aitrade.server.cache_store import CacheStore

    monkeypatch.setenv("AITRADE_PRECACHE_WATCHLIST_FILE", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("AITRADE_PRECACHE_SYMBOLS", "600519.SH, 000001.SZ")
    monkeypatch.setenv("AITRADE_PRECACHE_LOOKBACK_DAYS", "30")
    precache.remember_precache_symbol("300750.SZ")
    calls = []

    class FakeCollector:
        def __init__(self, store):
            self.store = store

        def collect_prices(self, symbol: str, start_date: str, end_date: str):
            calls.append((symbol, start_date, end_date))
            return {"records": [{"Date": end_date, "Close": 1}], "data_status": "complete"}

    result = precache.run_precache_once(
        cache_store=CacheStore(tmp_path / "cache"),
        collector_factory=FakeCollector,
        today=date(2026, 5, 31),
    )

    assert result == {"total": 3, "complete": 3, "missing": 0}
    assert calls == [
        ("000001.SZ", "2026-05-01", "2026-05-31"),
        ("300750.SZ", "2026-05-01", "2026-05-31"),
        ("600519.SH", "2026-05-01", "2026-05-31"),
    ]


@pytest.mark.unit
def test_precache_scheduler_is_disabled_by_default(monkeypatch):
    from aitrade.server import precache

    monkeypatch.delenv("AITRADE_PRECACHE_ENABLED", raising=False)

    assert precache.start_precache_scheduler() is False
