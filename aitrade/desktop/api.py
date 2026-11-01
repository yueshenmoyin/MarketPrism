from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aitrade.dataflows.china_stock import (
    clear_china_stock_price_cache,
    format_user_facing_error,
    search_cn_stocks,
    set_china_stock_status_callback,
)
from aitrade.dataflows.config import get_config, set_config
from aitrade.default_config import DEFAULT_CONFIG

_DESKTOP_CONFIG_ENV = "AITRADE_DESKTOP_CONFIG"
Market = Literal["cn_a_share", "us_stock", "hk_stock"]
CnStockVendor = Literal["akshare", "tushare", "baostock"]
_RATING_LABELS = {
    "Buy": "买入",
    "Overweight": "增持",
    "Hold": "持有",
    "Underweight": "减持",
    "Sell": "卖出",
}
_REPORT_LABELS = {
    "Rating": "评级",
    "Recommendation": "投资建议",
    "Action": "操作",
    "Final Rating": "最终评级",
    "Executive Summary": "执行摘要",
    "Investment Thesis": "投资逻辑",
    "Price Target": "目标价",
    "Time Horizon": "投资周期",
    "Rationale": "理由",
    "Strategic Actions": "策略动作",
}


class DesktopConfigUpdate(BaseModel):
    llm_provider: str | None = None
    backend_url: str | None = None
    custom_base_url: str | None = None
    custom_api_key: str | None = None
    auth_server_url: str | None = None
    quick_think_llm: str | None = None
    deep_think_llm: str | None = None
    output_language: str | None = None
    primary_market: Market | None = None
    cn_stock_vendor: CnStockVendor | None = None
    tushare_token: str | None = None
    results_dir: str | None = None
    server_analysis_enabled: bool | None = None
    max_debate_rounds: int | None = Field(default=None, ge=1, le=5)
    max_risk_discuss_rounds: int | None = Field(default=None, ge=1, le=5)


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    auth_server_url: str | None = None


class AnalysisStartRequest(BaseModel):
    ticker: str
    analysis_date: str | None = None
    analysts: list[str] = Field(default_factory=lambda: ["market", "social", "news", "fundamentals"])
    research_depth: int = Field(default=1, ge=1, le=5)


class AnalysisTask:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.events: list[dict[str, Any]] = []
        self._condition = threading.Condition()
        self._finished = False
        self.report: str | None = None
        self.report_id: str | None = None
        self.status = "pending"
        self.error: str | None = None

    def push(self, event: dict[str, Any]) -> None:
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def finish(self) -> None:
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def iter_events(self):
        index = 0
        while True:
            with self._condition:
                while index >= len(self.events) and not self._finished:
                    self._condition.wait()
                if index < len(self.events):
                    event = self.events[index]
                    index += 1
                else:
                    break
            yield event


class AnalysisManager:
    def __init__(self):
        self._tasks: dict[str, AnalysisTask] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> AnalysisTask:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="未找到分析任务。") from exc

    def start(self, request: AnalysisStartRequest) -> AnalysisTask:
        with self._lock:
            if any(task.status in {"pending", "running"} for task in self._tasks.values()):
                raise HTTPException(status_code=409, detail="已有分析任务正在运行，请等待完成后再启动新的分析。")
            task = AnalysisTask(str(uuid.uuid4()))
            self._tasks[task.task_id] = task
        thread = threading.Thread(target=self._run_analysis, args=(task, request), daemon=True)
        thread.start()
        return task

    def _run_analysis(self, task: AnalysisTask, request: AnalysisStartRequest) -> None:
        from aitrade.graph.trading_graph import AITradeGraph

        task.status = "running"
        task.push({"type": "status", "message": "分析任务已启动。"})
        try:
            config = get_config()
            config["max_debate_rounds"] = request.research_depth
            config["max_risk_discuss_rounds"] = request.research_depth
            clear_china_stock_price_cache()
            set_china_stock_status_callback(lambda message: task.push({"type": "status", "message": message}))
            analysis_date = request.analysis_date or datetime.now().strftime("%Y-%m-%d")
            graph = AITradeGraph(
                selected_analysts=request.analysts,
                config=config,
                debug=False,
            )
            final_state, decision = graph.propagate(request.ticker, analysis_date)
            task.report = _render_report(request.ticker, analysis_date, final_state, decision)
            task.report_id = _save_report_file(request.ticker, analysis_date, task.report).name
            task.status = "completed"
            task.push({"type": "completed", "message": "分析完成。", "decision": decision, "report_id": task.report_id})
        except Exception as exc:
            task.status = "failed"
            task.error = format_user_facing_error(exc)
            task.push({"type": "error", "message": task.error})
        finally:
            set_china_stock_status_callback(None)
            clear_china_stock_price_cache()
            task.finish()


def _safe_config(config: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(config)
    if payload.get("custom_api_key"):
        payload["custom_api_key"] = "***"
    if payload.get("tushare_token"):
        payload["tushare_token"] = "***"
    if payload.get("auth_token"):
        payload["auth_token"] = "***"
    return payload


def _apply_runtime_secrets(config: dict[str, Any]) -> None:
    tushare_token = config.get("tushare_token")
    if isinstance(tushare_token, str) and tushare_token:
        os.environ["TUSHARE_TOKEN"] = tushare_token


def _desktop_config_path() -> Path:
    configured = os.environ.get(_DESKTOP_CONFIG_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".aitrade" / "desktop_config.json"


def _load_desktop_config() -> dict[str, Any]:
    path = _desktop_config_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as config_file:
        payload = json.load(config_file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid desktop config file: {path}")
    return payload


def _save_desktop_config(config: dict[str, Any]) -> None:
    path = _desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2)


def _apply_config_update(update: DesktopConfigUpdate) -> dict[str, Any]:
    incoming = update.model_dump(exclude_none=True)
    if "custom_base_url" in incoming:
        incoming["backend_url"] = incoming["custom_base_url"]
    if "primary_market" in incoming:
        incoming.setdefault("data_vendors", {}).update(_data_vendors_for_market(incoming["primary_market"]))
    if "cn_stock_vendor" in incoming:
        incoming.setdefault("data_vendors", {})["cn_stock"] = incoming.pop("cn_stock_vendor")
    set_config(incoming)
    updated = get_config()
    _apply_runtime_secrets(updated)
    _save_desktop_config(updated)
    return updated


def _normalize_auth_server_url(auth_server_url: str | None) -> str:
    cleaned = (auth_server_url or "").strip().rstrip("/")
    if not cleaned:
        raise HTTPException(status_code=400, detail="登录服务未配置，请联系管理员。")
    if not cleaned.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="授权服务器地址必须以 http:// 或 https:// 开头。")
    return cleaned


def _clear_auth_session() -> dict[str, Any]:
    set_config({"auth_token": None, "auth_user": None, "auth_expires_at": None})
    updated = get_config()
    _save_desktop_config(updated)
    return updated


def _auth_session(config: dict[str, Any] | None = None, verify_remote: bool = True) -> dict[str, Any]:
    current = config or get_config()
    if current.get("auth_token") and verify_remote:
        current = _verify_saved_auth_session(current)
    authenticated = bool(current.get("auth_token"))
    expires_at = current.get("auth_expires_at") if authenticated else None
    return {
        "authenticated": authenticated,
        "auth_server_url": current.get("auth_server_url"),
        "user": current.get("auth_user") if authenticated else None,
        "expires_at": expires_at,
        "expires_in_seconds": _auth_expires_in_seconds(expires_at),
    }


def _auth_expires_in_seconds(expires_at: Any) -> int | None:
    if not isinstance(expires_at, str) or not expires_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()))


def _require_authenticated_session() -> None:
    if not _auth_session()["authenticated"]:
        raise HTTPException(status_code=401, detail="请先登录后再启动分析。")


def _remote_server_base_and_token() -> tuple[str, str] | None:
    config = get_config()
    if config.get("server_analysis_enabled") is False:
        return None
    token = config.get("auth_token")
    if not isinstance(token, str) or not token.strip():
        return None
    auth_server_url = config.get("auth_server_url")
    if not isinstance(auth_server_url, str) or not auth_server_url.strip():
        return None
    base_url = _normalize_auth_server_url(auth_server_url)
    return base_url, token.strip()


def _remote_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _remote_analysis_start(request: AnalysisStartRequest) -> dict[str, Any] | None:
    target = _remote_server_base_and_token()
    if target is None:
        return None
    base_url, token = target
    try:
        response = requests.post(
            f"{base_url}/analysis/start",
            json=request.model_dump(exclude_none=True),
            headers=_remote_auth_headers(token),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"分析服务器连接失败：{format_user_facing_error(exc)}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502, detail=_extract_error_detail(response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="分析服务器返回的不是有效 JSON。") from exc
    if not isinstance(payload, dict) or not payload.get("analysis_id"):
        raise HTTPException(status_code=502, detail="分析服务器返回格式不正确。")
    payload["mode"] = "remote"
    return payload


def _remote_get_json(path: str) -> dict[str, Any] | None:
    target = _remote_server_base_and_token()
    if target is None:
        return None
    base_url, token = target
    try:
        response = requests.get(f"{base_url}{path}", headers=_remote_auth_headers(token), timeout=30)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"分析服务器连接失败：{format_user_facing_error(exc)}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502, detail=_extract_error_detail(response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="分析服务器返回的不是有效 JSON。") from exc
    return payload if isinstance(payload, dict) else None


def _remote_get_json_or_none_on_not_found(path: str) -> dict[str, Any] | None:
    try:
        return _remote_get_json(path)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _remote_event_stream(path: str):
    target = _remote_server_base_and_token()
    if target is None:
        return None
    base_url, token = target

    def iterator():
        try:
            with requests.get(f"{base_url}{path}", headers=_remote_auth_headers(token), timeout=(10, None), stream=True) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if line is not None:
                        yield f"{line}\n"
        except requests.RequestException as exc:
            event = {"type": "error", "message": f"分析服务器连接失败：{format_user_facing_error(exc)}"}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return iterator()


def _verify_saved_auth_session(config: dict[str, Any]) -> dict[str, Any]:
    token = config.get("auth_token")
    if not isinstance(token, str) or not token.strip():
        return _clear_auth_session()
    try:
        auth_server_url = _normalize_auth_server_url(config.get("auth_server_url"))
        payload = _post_auth_verify(auth_server_url, token)
    except HTTPException:
        return _clear_auth_session()

    user = payload.get("user")
    if not isinstance(user, dict):
        user = config.get("auth_user") if isinstance(config.get("auth_user"), dict) else None
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str):
        expires_at = None
    verified_token = payload.get("access_token") or payload.get("token") or token
    if not isinstance(verified_token, str) or not verified_token.strip():
        return _clear_auth_session()
    set_config(
        {
            "auth_token": verified_token,
            "auth_user": user,
            "auth_expires_at": expires_at,
            "auth_server_url": auth_server_url,
        }
    )
    updated = get_config()
    _save_desktop_config(updated)
    return updated


def _extract_error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or "登录服务器拒绝了请求。"
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if detail:
            return format_user_facing_error(RuntimeError(str(detail)))
    return "登录服务器拒绝了请求。"


def _post_auth_login(auth_server_url: str, username: str, password: str) -> dict[str, Any]:
    try:
        response = requests.post(
            f"{auth_server_url}/auth/login",
            json={"username": username, "password": password},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"登录服务器连接失败: {format_user_facing_error(exc)}") from exc

    if response.status_code >= 400:
        status_code = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status_code=status_code, detail=_extract_error_detail(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="登录服务器返回的不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="登录服务器返回格式不正确。")
    return payload


def _post_auth_verify(auth_server_url: str, token: str) -> dict[str, Any]:
    try:
        response = requests.post(
            f"{auth_server_url}/auth/verify",
            json={"token": token},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"登录服务器连接失败: {format_user_facing_error(exc)}") from exc

    if response.status_code >= 400:
        status_code = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status_code=status_code, detail=_extract_error_detail(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="登录服务器返回的不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="登录服务器返回格式不正确。")
    return payload


def _apply_auth_login(request: AuthLoginRequest) -> dict[str, Any]:
    username = request.username.strip()
    if not username or not request.password:
        raise HTTPException(status_code=400, detail="请输入账号和密码。")

    current = get_config()
    auth_server_url = _normalize_auth_server_url(request.auth_server_url or current.get("auth_server_url"))
    payload = _post_auth_login(auth_server_url, username, request.password)
    token = payload.get("access_token") or payload.get("token") or payload.get("session_token")
    if not isinstance(token, str) or not token.strip():
        raise HTTPException(status_code=502, detail="登录服务器未返回 access_token、token 或 session_token。")

    user = payload.get("user")
    if not isinstance(user, dict):
        user = {"name": str(payload.get("name") or payload.get("username") or username)}

    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str):
        expires_at = None
    set_config({"auth_server_url": auth_server_url, "auth_token": token, "auth_user": user, "auth_expires_at": expires_at})
    updated = get_config()
    _save_desktop_config(updated)
    return _auth_session(updated, verify_remote=False)


def _apply_auth_logout() -> dict[str, Any]:
    updated = _clear_auth_session()
    return _auth_session(updated)


def _data_vendors_for_market(market: str) -> dict[str, str]:
    if market == "cn_a_share":
        vendor = "china_stock"
    else:
        vendor = "yfinance"
    return {
        "core_stock_apis": vendor,
        "technical_indicators": vendor,
        "fundamental_data": vendor,
        "news_data": vendor,
    }


def _search_yfinance_compatible_stocks(query: str, market: Market) -> list[dict[str, str]]:
    normalized = query.strip().upper()
    if not normalized:
        return []

    if market == "us_stock":
        symbol = normalized.replace(" ", "")
        return [{"symbol": symbol, "code": symbol, "name": symbol, "market": "美股"}]

    if market == "hk_stock":
        raw = normalized[:-3] if normalized.endswith(".HK") else normalized
        code = raw.zfill(4) if raw.isdigit() else raw
        symbol = code if code.endswith(".HK") else f"{code}.HK"
        return [{"symbol": symbol, "code": code.replace(".HK", ""), "name": symbol, "market": "港股"}]

    return []


def _localize_rating(value: str) -> str:
    return _RATING_LABELS.get(value.strip(), value)


def _localize_report_text(text: str) -> str:
    localized = text
    for english, chinese in _REPORT_LABELS.items():
        localized = localized.replace(f"**{english}**:", f"**{chinese}**：")
        localized = localized.replace(f"{english}:", f"{chinese}：")

    rating_values = "|".join(re.escape(value) for value in _RATING_LABELS)
    label_names = "|".join(re.escape(value) for value in _REPORT_LABELS.values())
    localized = re.sub(rf"((?:\*\*(?:{label_names})\*\*|(?:{label_names}))：)\s+", r"\1", localized)
    localized = re.sub(rf"((?:-\s*)?最终评级[:：]\s*)({rating_values})\b", lambda match: f"{match.group(1)}{_localize_rating(match.group(2))}", localized)
    pattern = re.compile(rf"((?:\*\*(?:{label_names})\*\*|(?:{label_names}))：\s*)({rating_values})\b")
    return pattern.sub(lambda match: f"{match.group(1)}{_localize_rating(match.group(2))}", localized)


def _reports_dir() -> Path:
    directory = Path(get_config().get("results_dir") or DEFAULT_CONFIG["results_dir"]) / "desktop_reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value.strip())
    return cleaned.strip("._") or "report"


def _report_title(report: str, ticker: str, analysis_date: str) -> str:
    for line in report.splitlines():
        title = line.strip().lstrip("#").strip()
        if title:
            return title
    return f"{ticker} {analysis_date} 分析报告"


def _report_file_path(report_id: str) -> Path:
    if Path(report_id).name != report_id or not report_id.endswith(".md"):
        raise HTTPException(status_code=400, detail="报告编号不正确。")
    reports_dir = _reports_dir().resolve()
    path = (reports_dir / report_id).resolve()
    if os.path.commonpath([str(reports_dir), str(path)]) != str(reports_dir):
        raise HTTPException(status_code=400, detail="报告编号不正确。")
    if not path.exists():
        raise HTTPException(status_code=404, detail="未找到历史报告。")
    return path


def _report_summary(path: Path) -> dict[str, Any]:
    report = path.read_text(encoding="utf-8")
    stem_parts = path.stem.split("__")
    ticker = stem_parts[1] if len(stem_parts) >= 2 else ""
    analysis_date = stem_parts[2] if len(stem_parts) >= 3 else ""
    created_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    return {
        "id": path.name,
        "title": _report_title(report, ticker, analysis_date),
        "ticker": ticker,
        "analysis_date": analysis_date,
        "created_at": created_at,
    }


def _save_report_file(ticker: str, analysis_date: str, report: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = "__".join(
        [
            timestamp,
            _safe_filename_part(ticker),
            _safe_filename_part(analysis_date),
        ]
    )
    path = _reports_dir() / f"{filename}.md"
    path.write_text(report, encoding="utf-8")
    return path


def _list_report_history(limit: int = 30) -> list[dict[str, Any]]:
    reports = sorted(_reports_dir().glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [_report_summary(path) for path in reports[:limit]]


def _read_report_history(report_id: str) -> dict[str, Any]:
    path = _report_file_path(report_id)
    report = path.read_text(encoding="utf-8")
    payload = _report_summary(path)
    payload["report"] = _localize_report_text(report)
    return payload


def _render_report(ticker: str, analysis_date: str, final_state: dict[str, Any], decision: str) -> str:
    sections = [
        f"# {ticker} 股票分析报告",
        f"- 分析日期: {analysis_date}",
        f"- 最终评级: {_localize_rating(decision)}",
    ]
    report_map = [
        ("市场技术分析", "market_report"),
        ("情绪分析", "sentiment_report"),
        ("新闻分析", "news_report"),
        ("基本面分析", "fundamentals_report"),
        ("研究团队结论", "investment_plan"),
        ("交易员计划", "trader_investment_plan"),
        ("组合经理决策", "final_trade_decision"),
    ]
    for title, key in report_map:
        content = final_state.get(key)
        if content:
            sections.append(f"## {title}\n\n{_localize_report_text(str(content))}")
    return "\n\n".join(sections)


def _stream_events(task: AnalysisTask):
    for event in task.iter_events():
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app() -> FastAPI:
    set_config(DEFAULT_CONFIG)
    persisted_config = _load_desktop_config()
    if persisted_config:
        set_config(persisted_config)
        _apply_runtime_secrets(persisted_config)
    manager = AnalysisManager()
    app = FastAPI(title="MarketPrism Desktop API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/config")
    def read_config() -> dict[str, Any]:
        return _safe_config(get_config())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "backend": "desktop", "version": "0.1.0"}

    @app.post("/config")
    def update_config(update: DesktopConfigUpdate) -> dict[str, Any]:
        return _safe_config(_apply_config_update(update))

    @app.get("/auth/session")
    def auth_session() -> dict[str, Any]:
        return _auth_session()

    @app.post("/auth/login")
    def auth_login(request: AuthLoginRequest) -> dict[str, Any]:
        return _apply_auth_login(request)

    @app.post("/auth/logout")
    def auth_logout() -> dict[str, Any]:
        return _apply_auth_logout()

    @app.get("/stocks/search")
    def stock_search(q: str, market: Market = "cn_a_share", vendor: CnStockVendor | None = None) -> dict[str, Any]:
        if not q.strip():
            return {"items": []}
        if market != "cn_a_share":
            return {"items": _search_yfinance_compatible_stocks(q, market)}
        try:
            return {"items": search_cn_stocks(q, vendor_name=vendor)}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"股票搜索失败: {format_user_facing_error(exc)}") from exc

    @app.post("/analysis/start")
    def start_analysis(request: AnalysisStartRequest) -> dict[str, str]:
        _require_authenticated_session()
        remote_payload = _remote_analysis_start(request)
        if remote_payload is not None:
            return remote_payload
        task = manager.start(request)
        return {"analysis_id": task.task_id, "status": task.status}

    @app.get("/analysis/{analysis_id}/events")
    def analysis_events(analysis_id: str):
        remote_stream = _remote_event_stream(f"/analysis/{analysis_id}/events")
        if remote_stream is not None:
            return StreamingResponse(remote_stream, media_type="text/event-stream")
        task = manager.get(analysis_id)
        return StreamingResponse(_stream_events(task), media_type="text/event-stream")

    @app.get("/analysis/{analysis_id}/report")
    def analysis_report(analysis_id: str) -> dict[str, Any]:
        remote_payload = _remote_get_json(f"/analysis/{analysis_id}/report")
        if remote_payload is not None:
            if remote_payload.get("report"):
                remote_payload["report"] = _localize_report_text(str(remote_payload["report"]))
            return remote_payload
        task = manager.get(analysis_id)
        if task.status == "failed":
            raise HTTPException(status_code=500, detail=task.error or "分析失败。")
        report = _localize_report_text(task.report) if task.report else None
        return {"status": task.status, "report": report, "report_id": task.report_id}

    @app.get("/reports")
    def report_history() -> dict[str, Any]:
        remote_payload = _remote_get_json_or_none_on_not_found("/reports")
        if remote_payload is not None:
            return remote_payload
        return {"items": _list_report_history()}

    @app.get("/reports/{report_id}")
    def saved_report(report_id: str) -> dict[str, Any]:
        remote_payload = _remote_get_json_or_none_on_not_found(f"/reports/{report_id}")
        if remote_payload is not None:
            if remote_payload.get("report"):
                remote_payload["report"] = _localize_report_text(str(remote_payload["report"]))
            return remote_payload
        return _read_report_history(report_id)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("aitrade.desktop.api:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
