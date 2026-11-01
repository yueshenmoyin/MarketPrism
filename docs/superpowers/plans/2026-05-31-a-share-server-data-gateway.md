# A 股服务端数据网关实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AITrade 首版收窄成“A 股一键可用”的产品形态：普通客户不配置数据源、不配置模型、不导入文件，桌面端登录后通过服务器完成 A 股数据读取、缓存和报告生成。

**Architecture:** 桌面端只保留登录、选股、启动分析、进度、报告和 PDF 导出；授权服务器扩展为 A 股数据网关和报告任务服务。服务器后台定时采集免费 A 股公开数据并缓存，实时请求失败时优先使用缓存，客户端不直接依赖 AkShare/BaoStock 等免费源。

**Tech Stack:** Python FastAPI、现有 `aitrade.server.auth_api`、现有 `aitrade.dataflows.china_stock`、JSON/SQLite 本地缓存、Tauri + React 桌面端、pytest、Vitest。

---

## Scope

只做 A 股。港股、美股入口暂时保留在界面兼容层，但不接入服务端缓存网关，也不作为本阶段卖点。

本阶段不接券商、不自动下单、不做实时行情终端，不承诺分钟级行情。产品定位为“A 股盘后 AI 投研报告工具”。

## Target User Flow

1. 客户打开桌面端。
2. 输入账号密码登录。
3. 搜索 A 股股票。
4. 选择分析团队和分析深度。
5. 点击“开始分析”。
6. 服务器读取缓存数据，缺失时后台尝试补采。
7. 服务器生成报告并返回进度。
8. 客户查看中文报告并导出 PDF。

## File Structure

- Modify: `D:/Forex/AITrade/TradingAgents/aitrade/server/auth_api.py`
  - 保留登录、授权、后台管理。
  - 挂载数据网关和分析任务路由。

- Create/Modify: `D:/Forex/AITrade/TradingAgents/aitrade/server/data_gateway.py`
  - A 股股票搜索、日线行情、基础财务、新闻/公告摘要接口。
  - 统一鉴权、缓存、错误中文化。

- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/cache_store.py`
  - 封装 JSON/SQLite 缓存读写。
  - 管理 TTL、缓存命中、过期策略。

- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/a_share_collector.py`
  - 后台采集器。
  - 从 AkShare、BaoStock、AData、FinShare 等免费源采集 A 股数据。
  - 对外不暴露具体免费源名称。

- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/analysis_jobs.py`
  - 服务端分析任务队列 MVP。
  - 负责接收分析请求、运行现有 AITradeGraph、保存报告、提供 SSE 事件。

- Modify: `D:/Forex/AITrade/TradingAgents/aitrade/desktop/api.py`
  - 桌面本地 API 从“本地执行分析”改为“代理服务器分析”。
  - 保留本地模式作为开发兜底，但普通用户默认走服务器。

- Modify: `D:/Forex/AITrade/TradingAgents/src/App.tsx`
  - 隐藏普通用户不该看到的数据源配置。
  - A 股首版只显示股票搜索、模型状态、分析设置、报告。

- Modify: `D:/Forex/AITrade/TradingAgents/src/api.ts`
  - 增加服务端分析接口类型。
  - 保持前端调用本地 `127.0.0.1:8765`，由本地 API 代理远程服务器。

- Tests:
  - Create/Modify: `D:/Forex/AITrade/TradingAgents/tests/test_server_data_gateway.py`
  - Create: `D:/Forex/AITrade/TradingAgents/tests/test_server_cache_store.py`
  - Create: `D:/Forex/AITrade/TradingAgents/tests/test_server_a_share_collector.py`
  - Create: `D:/Forex/AITrade/TradingAgents/tests/test_server_analysis_jobs.py`
  - Modify: `D:/Forex/AITrade/TradingAgents/tests/test_desktop_api.py`
  - Modify: `D:/Forex/AITrade/TradingAgents/src/api.test.ts`

---

## Task 1: Commit Current Gateway Checkpoint

**Files:**
- Existing dirty files from the previous gateway MVP.

- [ ] **Step 1: Check working tree**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents status --short
```

Expected: shows only intended gateway MVP and plan files.

- [ ] **Step 2: Run current verification**

Run:

```powershell
python -m pytest tests/test_server_data_gateway.py tests/test_auth_server_api.py tests/test_china_stock_data.py tests/test_desktop_api.py tests/test_dataflows_config.py -q -p no:cacheprovider --basetemp build\tmp-pytest-a-share-gateway-checkpoint
npm.cmd run test:frontend
npm.cmd run build
```

Expected: pytest passes, Vitest passes, Vite build passes.

- [ ] **Step 3: Commit checkpoint**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add aitrade\dataflows\china_stock.py aitrade\default_config.py aitrade\server\auth_api.py aitrade\server\data_gateway.py tests\test_china_stock_data.py tests\test_server_data_gateway.py docs\superpowers\plans\2026-05-31-a-share-server-data-gateway.md
git -C D:\Forex\AITrade\TradingAgents commit -m "Add A-share server data gateway MVP"
```

Expected: one commit created. Do not stage `deploy.local.json`, `build/`, `dist/`, `src-tauri/target/`, or packaged binaries.

---

## Task 2: Add Cache Store Abstraction

**Files:**
- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/cache_store.py`
- Test: `D:/Forex/AITrade/TradingAgents/tests/test_server_cache_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_server_cache_store.py`:

```python
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.unit
def test_cache_store_round_trips_json_payload(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    store.write("prices", ["600519.SH", "2026-05-01"], {"records": [{"Close": 1695.2}]})

    assert store.read("prices", ["600519.SH", "2026-05-01"], ttl_seconds=86400) == {
        "records": [{"Close": 1695.2}]
    }


@pytest.mark.unit
def test_cache_store_returns_none_for_expired_payload(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    path = store.write("prices", ["600519.SH"], {"records": []})
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    path.touch()
    import os
    os.utime(path, (old_time, old_time))

    assert store.read("prices", ["600519.SH"], ttl_seconds=60) is None


@pytest.mark.unit
def test_cache_store_keeps_different_chinese_queries_separate(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    store.write("search", ["茅台"], {"items": [{"symbol": "600519.SH"}]})
    store.write("search", ["平安"], {"items": [{"symbol": "000001.SZ"}]})

    assert store.read("search", ["茅台"], ttl_seconds=86400)["items"][0]["symbol"] == "600519.SH"
    assert store.read("search", ["平安"], ttl_seconds=86400)["items"][0]["symbol"] == "000001.SZ"
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_server_cache_store.py -q -p no:cacheprovider --basetemp build\tmp-pytest-cache-store-red
```

Expected: FAIL because `aitrade.server.cache_store` does not exist.

- [ ] **Step 3: Implement `CacheStore`**

Create `aitrade/server/cache_store.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
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
        if ttl_seconds > 0:
            import time

            if time.time() - path.stat().st_mtime > ttl_seconds:
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
```

- [ ] **Step 4: Verify tests pass**

Run:

```powershell
python -m pytest tests/test_server_cache_store.py -q -p no:cacheprovider --basetemp build\tmp-pytest-cache-store-green
```

Expected: PASS.

- [ ] **Step 5: Refactor data gateway to use `CacheStore`**

Modify `aitrade/server/data_gateway.py`:

```python
from aitrade.server.cache_store import CacheStore


def _cache_store() -> CacheStore:
    return CacheStore(_cache_dir())
```

Replace direct `_read_cache`, `_write_cache`, `_cache_path` usage with `CacheStore.read()` and `CacheStore.write()`.

- [ ] **Step 6: Verify gateway tests**

Run:

```powershell
python -m pytest tests/test_server_data_gateway.py tests/test_server_cache_store.py -q -p no:cacheprovider --basetemp build\tmp-pytest-cache-store-gateway
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add aitrade\server\cache_store.py aitrade\server\data_gateway.py tests\test_server_cache_store.py tests\test_server_data_gateway.py
git -C D:\Forex\AITrade\TradingAgents commit -m "Add server cache store"
```

---

## Task 3: Add A 股 Data Collector

**Files:**
- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/a_share_collector.py`
- Test: `D:/Forex/AITrade/TradingAgents/tests/test_server_a_share_collector.py`

- [ ] **Step 1: Write failing collector tests**

Create `tests/test_server_a_share_collector.py`:

```python
import pandas as pd
import pytest


@pytest.mark.unit
def test_collector_caches_price_data_from_first_available_provider(monkeypatch, tmp_path):
    from aitrade.server.a_share_collector import AShareCollector
    from aitrade.server.cache_store import CacheStore

    calls = []

    class FailingProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            calls.append("fail")
            raise RuntimeError("temporary failure")

    class WorkingProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            calls.append("work")
            return pd.DataFrame([{"Date": "2026-05-18", "Close": 1695.2}])

    collector = AShareCollector(CacheStore(tmp_path), providers=[FailingProvider(), WorkingProvider()])
    payload = collector.collect_prices("600519.SH", "2026-05-18", "2026-05-23")

    assert calls == ["fail", "work"]
    assert payload["records"][0]["Close"] == 1695.2
    assert payload["data_status"] == "complete"


@pytest.mark.unit
def test_collector_returns_partial_status_for_empty_data(tmp_path):
    from aitrade.server.a_share_collector import AShareCollector
    from aitrade.server.cache_store import CacheStore

    class EmptyProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            return pd.DataFrame()

    collector = AShareCollector(CacheStore(tmp_path), providers=[EmptyProvider()])
    payload = collector.collect_prices("600519.SH", "2026-05-18", "2026-05-23")

    assert payload["records"] == []
    assert payload["data_status"] == "missing"
    assert "公开数据暂时不足" in payload["message"]
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_server_a_share_collector.py -q -p no:cacheprovider --basetemp build\tmp-pytest-collector-red
```

Expected: FAIL because `aitrade.server.a_share_collector` does not exist.

- [ ] **Step 3: Implement collector**

Create `aitrade/server/a_share_collector.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from aitrade.dataflows.china_stock import (
    ADataChinaStockProvider,
    AkShareChinaStockProvider,
    BaoStockChinaStockProvider,
    FinShareChinaStockProvider,
    normalize_cn_stock_symbol,
)
from aitrade.server.cache_store import CacheStore


class AShareCollector:
    def __init__(self, cache_store: CacheStore, providers: list[Any] | None = None):
        self.cache_store = cache_store
        self.providers = providers or [
            AkShareChinaStockProvider(),
            BaoStockChinaStockProvider(),
            FinShareChinaStockProvider(),
            ADataChinaStockProvider(),
        ]

    def collect_prices(self, symbol: str, start_date: str, end_date: str) -> dict[str, Any]:
        normalized = normalize_cn_stock_symbol(symbol)
        for provider in self.providers:
            try:
                df = provider.get_price_dataframe(normalized, start_date, end_date)
            except Exception:
                continue
            payload = self._payload(normalized, start_date, end_date, df)
            self.cache_store.write("prices", [normalized, start_date, end_date], payload)
            return payload
        payload = self._payload(normalized, start_date, end_date, pd.DataFrame())
        self.cache_store.write("prices", [normalized, start_date, end_date], payload)
        return payload

    def _payload(self, symbol: str, start_date: str, end_date: str, df: pd.DataFrame) -> dict[str, Any]:
        records = [] if df.empty else df.where(pd.notna(df), None).to_dict(orient="records")
        return {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "records": records,
            "data_status": "complete" if records else "missing",
            "message": "" if records else "公开数据暂时不足，系统会稍后自动重试。",
            "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
```

- [ ] **Step 4: Verify collector tests pass**

Run:

```powershell
python -m pytest tests/test_server_a_share_collector.py -q -p no:cacheprovider --basetemp build\tmp-pytest-collector-green
```

Expected: PASS.

- [ ] **Step 5: Wire collector into data gateway**

Modify `aitrade/server/data_gateway.py` so `/data/cn/prices`:

1. reads fresh cache first;
2. if missing, calls `AShareCollector.collect_prices()`;
3. returns `"source": "cache"` or `"source": "live"` but does not expose which free provider was used.

- [ ] **Step 6: Add gateway test for missing live data**

Append to `tests/test_server_data_gateway.py`:

```python
@pytest.mark.unit
def test_server_data_gateway_returns_chinese_partial_payload_when_public_data_missing(monkeypatch):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app

    def fake_fetch(symbol: str, start_date: str, end_date: str):
        import pandas as pd
        return pd.DataFrame()

    monkeypatch.setattr(data_gateway, "_fetch_price_dataframe", fake_fetch)

    client = TestClient(create_app())
    headers = _authorized_headers(client)
    response = client.get(
        "/data/cn/prices",
        params={"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    assert payload["data_status"] == "missing"
    assert "公开数据" in payload["message"]
```

- [ ] **Step 7: Verify gateway tests**

Run:

```powershell
python -m pytest tests/test_server_data_gateway.py tests/test_server_a_share_collector.py -q -p no:cacheprovider --basetemp build\tmp-pytest-collector-gateway
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add aitrade\server\a_share_collector.py aitrade\server\data_gateway.py tests\test_server_a_share_collector.py tests\test_server_data_gateway.py
git -C D:\Forex\AITrade\TradingAgents commit -m "Add A-share server collector"
```

---

## Task 4: Move A 股 Analysis Execution to Server

**Files:**
- Create: `D:/Forex/AITrade/TradingAgents/aitrade/server/analysis_jobs.py`
- Modify: `D:/Forex/AITrade/TradingAgents/aitrade/server/auth_api.py`
- Modify: `D:/Forex/AITrade/TradingAgents/aitrade/desktop/api.py`
- Test: `D:/Forex/AITrade/TradingAgents/tests/test_server_analysis_jobs.py`
- Test: `D:/Forex/AITrade/TradingAgents/tests/test_desktop_api.py`

- [ ] **Step 1: Write server analysis job tests**

Create `tests/test_server_analysis_jobs.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    monkeypatch.setenv("AITRADE_AUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("AITRADE_RESULTS_DIR", str(tmp_path / "reports"))


def _login_headers(client):
    client.post(
        "/admin/api/users",
        auth=("admin", "admin-secret"),
        json={"username": "demo", "password": "secret", "expires_at": "2099-01-01"},
    )
    token = client.post("/auth/login", json={"username": "demo", "password": "secret"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
def test_server_analysis_job_returns_task_id(monkeypatch):
    from aitrade.server import analysis_jobs
    from aitrade.server.auth_api import create_app

    def fake_run_job(task, request):
        task.status = "completed"
        task.report = "# 600519.SH 股票分析报告\n\n模拟报告"
        task.push({"type": "completed", "message": "分析完成。", "report_id": "mock.md"})
        task.finish()

    monkeypatch.setattr(analysis_jobs.AnalysisJobManager, "_run_job", fake_run_job)

    client = TestClient(create_app())
    headers = _login_headers(client)
    response = client.post(
        "/analysis/start",
        headers=headers,
        json={"ticker": "600519.SH", "analysis_date": "2026-05-23", "analysts": ["market"], "research_depth": 1},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"]


@pytest.mark.unit
def test_server_analysis_job_requires_login():
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.post("/analysis/start", json={"ticker": "600519.SH"})

    assert response.status_code in {401, 403}
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest tests/test_server_analysis_jobs.py -q -p no:cacheprovider --basetemp build\tmp-pytest-server-analysis-red
```

Expected: FAIL because server analysis route does not exist.

- [ ] **Step 3: Implement server analysis job module**

Create `aitrade/server/analysis_jobs.py` by extracting the existing `AnalysisTask`, `AnalysisManager`, `_stream_events`, `_save_report_file`, `_render_report` pattern from `aitrade/desktop/api.py`, but protect routes with `_require_data_user`.

The module must register:

```python
POST /analysis/start
GET /analysis/{analysis_id}/events
GET /analysis/{analysis_id}/report
GET /reports
GET /reports/{report_id}
```

- [ ] **Step 4: Mount server analysis routes**

Modify `aitrade/server/auth_api.py`:

```python
from aitrade.server.analysis_jobs import register_analysis_routes

register_analysis_routes(app)
```

- [ ] **Step 5: Verify server tests pass**

Run:

```powershell
python -m pytest tests/test_server_analysis_jobs.py tests/test_auth_server_api.py -q -p no:cacheprovider --basetemp build\tmp-pytest-server-analysis-green
```

Expected: PASS.

- [ ] **Step 6: Modify desktop local API to proxy server analysis**

In `aitrade/desktop/api.py`, update `/analysis/start`:

1. verify saved session;
2. if `server_analysis_enabled` is true and `auth_server_url` exists, POST to `{auth_server_url}/analysis/start`;
3. return remote `analysis_id` with marker `"mode": "remote"`;
4. local `/analysis/{id}/events` proxies remote SSE;
5. local `/analysis/{id}/report` proxies remote report.

Keep current local `AnalysisManager` as development fallback only.

- [ ] **Step 7: Add desktop proxy test**

Append to `tests/test_desktop_api.py`:

```python
@pytest.mark.unit
def test_desktop_api_proxies_analysis_start_to_server(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://api.example.com",
                "auth_token": "token",
                "auth_user": {"username": "demo"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
                "server_analysis_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))

    monkeypatch.setattr(
        desktop_api,
        "_post_auth_verify",
        lambda auth_server_url, token: {"access_token": token, "user": {"username": "demo"}, "expires_at": "2099-01-01T00:00:00Z"},
        raising=False,
    )

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"analysis_id": "remote-1", "status": "running"}

    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return Response()

    monkeypatch.setattr(desktop_api.requests, "post", fake_post)

    client = TestClient(desktop_api.create_app())
    response = client.post("/analysis/start", json={"ticker": "600519.SH", "analysts": ["market"], "research_depth": 1})

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "remote-1"
    assert calls[0][0] == "https://api.example.com/analysis/start"
    assert calls[0][2] == {"Authorization": "Bearer token"}
```

- [ ] **Step 8: Verify desktop proxy tests**

Run:

```powershell
python -m pytest tests/test_desktop_api.py::test_desktop_api_proxies_analysis_start_to_server -q -p no:cacheprovider --basetemp build\tmp-pytest-desktop-proxy
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add aitrade\server\analysis_jobs.py aitrade\server\auth_api.py aitrade\desktop\api.py tests\test_server_analysis_jobs.py tests\test_desktop_api.py
git -C D:\Forex\AITrade\TradingAgents commit -m "Run A-share analysis on server"
```

---

## Task 5: Simplify Desktop UI for 普通客户 A 股版

**Files:**
- Modify: `D:/Forex/AITrade/TradingAgents/src/App.tsx`
- Modify: `D:/Forex/AITrade/TradingAgents/src/api.ts`
- Modify: `D:/Forex/AITrade/TradingAgents/src/api.test.ts`

- [ ] **Step 1: Write frontend tests**

Modify `src/api.test.ts` or create a focused UI test if existing test style supports it:

```ts
import { describe, expect, it } from "vitest";
import { shouldShowAdvancedDataControls } from "./api";

describe("A 股普通客户模式", () => {
  it("默认隐藏数据源配置", () => {
    expect(shouldShowAdvancedDataControls({ primary_market: "cn_a_share", advanced_mode: false })).toBe(false);
  });

  it("高级模式才显示数据源配置", () => {
    expect(shouldShowAdvancedDataControls({ primary_market: "cn_a_share", advanced_mode: true })).toBe(true);
  });
});
```

- [ ] **Step 2: Verify frontend test fails**

Run:

```powershell
npm.cmd run test:frontend
```

Expected: FAIL because `shouldShowAdvancedDataControls` does not exist.

- [ ] **Step 3: Implement helper**

Modify `src/api.ts`:

```ts
export function shouldShowAdvancedDataControls(config: { primary_market: Market; advanced_mode?: boolean }): boolean {
  return config.primary_market === "cn_a_share" && config.advanced_mode === true;
}
```

- [ ] **Step 4: Update UI**

Modify `src/App.tsx`:

1. Default A 股普通模式 hides AkShare/BaoStock/Tushare buttons.
2. Show a simple label: `数据：服务器公开数据缓存`.
3. Keep an internal advanced toggle only if already present; do not add a new visible complex settings area.

- [ ] **Step 5: Verify frontend**

Run:

```powershell
npm.cmd run test:frontend
npm.cmd run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add src\App.tsx src\api.ts src\api.test.ts
git -C D:\Forex\AITrade\TradingAgents commit -m "Simplify A-share desktop data controls"
```

---

## Task 6: Add Server Deployment Notes for A 股 Data Cache

**Files:**
- Modify: `D:/Forex/AITrade/TradingAgents/README.md`
- Create: `D:/Forex/AITrade/TradingAgents/docs/a-share-server-deployment.md`

- [ ] **Step 1: Write deployment doc**

Create `docs/a-share-server-deployment.md` with:

```markdown
# AITrade A 股服务器部署说明

## 服务器职责

- 授权登录
- A 股公开数据缓存
- A 股分析任务
- 报告保存

## 必填环境变量

- `AITRADE_AUTH_USERNAME`
- `AITRADE_AUTH_PASSWORD`
- `AITRADE_AUTH_USERS_FILE`
- `AITRADE_DATA_CACHE_DIR`
- `AITRADE_RESULTS_DIR`

## 推荐定位

本产品定位为 A 股盘后 AI 投研报告工具，不是实时行情终端。

## 数据说明

服务器会使用公开免费数据源进行缓存。公开数据可能延迟、缺失或临时不可用；系统会使用缓存和多源重试降低影响。
```

- [ ] **Step 2: Add README link**

Modify `README.md`, add:

```markdown
服务器部署请参考 [A 股服务器部署说明](docs/a-share-server-deployment.md)。
```

- [ ] **Step 3: Verify docs paths**

Run:

```powershell
Test-Path D:\Forex\AITrade\TradingAgents\docs\a-share-server-deployment.md
```

Expected: `True`.

- [ ] **Step 4: Commit**

Run:

```powershell
git -C D:\Forex\AITrade\TradingAgents add README.md docs\a-share-server-deployment.md
git -C D:\Forex\AITrade\TradingAgents commit -m "Document A-share server deployment"
```

---

## Final Verification

Run:

```powershell
python -m pytest tests/test_server_cache_store.py tests/test_server_a_share_collector.py tests/test_server_data_gateway.py tests/test_server_analysis_jobs.py tests/test_auth_server_api.py tests/test_desktop_api.py tests/test_china_stock_data.py -q -p no:cacheprovider --basetemp build\tmp-pytest-a-share-server-final
npm.cmd run test:frontend
npm.cmd run build
git -C D:\Forex\AITrade\TradingAgents diff --check
git -C D:\Forex\AITrade\TradingAgents status --short
```

Expected:

- pytest passes.
- Vitest passes.
- Vite build passes.
- `git diff --check` has no errors except harmless CRLF warnings.
- Working tree is clean after commits.

## Non-Goals

- 不做港股/美股服务端缓存。
- 不做实时行情终端。
- 不做分钟线。
- 不接券商。
- 不做自动下单。
- 不把 Tushare/iFinD/Choice 作为本阶段必需项。
- 不让普通客户配置数据源或模型 Key。

## Product Copy Rules

客户端应使用这些表达：

- `服务器公开数据缓存`
- `A 股盘后 AI 投研报告`
- `公开数据可能存在延迟或缺失`
- `系统会自动使用可用数据生成报告`

客户端不应使用这些表达：

- `实时行情`
- `交易信号`
- `保证准确`
- `官方行情数据`
- `自动推荐买入`

