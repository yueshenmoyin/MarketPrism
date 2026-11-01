from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aitrade.dataflows.china_stock import clear_china_stock_price_cache, format_user_facing_error
from aitrade.dataflows.config import get_config
from aitrade.default_config import DEFAULT_CONFIG
from aitrade.server.data_gateway import _require_data_user


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


class AnalysisJobManager:
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
            task = AnalysisTask(str(uuid.uuid4()))
            self._tasks[task.task_id] = task
        thread = threading.Thread(target=self._run_job, args=(task, request), daemon=True)
        thread.start()
        return task

    def _run_job(self, task: AnalysisTask, request: AnalysisStartRequest) -> None:
        from aitrade.graph.trading_graph import AITradeGraph

        task.status = "running"
        task.push({"type": "status", "message": "分析任务已启动。"})
        try:
            config = get_config()
            config["max_debate_rounds"] = request.research_depth
            config["max_risk_discuss_rounds"] = request.research_depth
            clear_china_stock_price_cache()
            analysis_date = request.analysis_date or datetime.now().strftime("%Y-%m-%d")
            graph = AITradeGraph(selected_analysts=request.analysts, config=config, debug=False)
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
            clear_china_stock_price_cache()
            task.finish()


def _reports_dir() -> Path:
    directory = Path(get_config().get("results_dir") or DEFAULT_CONFIG["results_dir"]) / "server_reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value.strip())
    return cleaned.strip("._") or "report"


def _save_report_file(ticker: str, analysis_date: str, report: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = "__".join([timestamp, _safe_filename_part(ticker), _safe_filename_part(analysis_date)])
    path = _reports_dir() / f"{filename}.md"
    path.write_text(report, encoding="utf-8")
    return path


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


def _report_title(report: str, ticker: str, analysis_date: str) -> str:
    for line in report.splitlines():
        title = line.strip().lstrip("#").strip()
        if title:
            return title
    return f"{ticker} {analysis_date} 分析报告"


def _report_summary(path: Path) -> dict[str, Any]:
    report = path.read_text(encoding="utf-8")
    stem_parts = path.stem.split("__")
    ticker = stem_parts[1] if len(stem_parts) >= 2 else ""
    analysis_date = stem_parts[2] if len(stem_parts) >= 3 else ""
    return {
        "id": path.name,
        "title": _report_title(report, ticker, analysis_date),
        "ticker": ticker,
        "analysis_date": analysis_date,
        "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    }


def _list_report_history(limit: int = 30) -> list[dict[str, Any]]:
    reports = sorted(_reports_dir().glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [_report_summary(path) for path in reports[:limit]]


def _read_report_history(report_id: str) -> dict[str, Any]:
    path = _report_file_path(report_id)
    payload = _report_summary(path)
    payload["report"] = path.read_text(encoding="utf-8")
    return payload


def _render_report(ticker: str, analysis_date: str, final_state: dict[str, Any], decision: str) -> str:
    sections = [
        f"# {ticker} 股票分析报告",
        f"- 分析日期: {analysis_date}",
        f"- 最终评级: {decision}",
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
            sections.append(f"## {title}\n\n{content}")
    return "\n\n".join(sections)


def _stream_events(task: AnalysisTask):
    for event in task.iter_events():
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def register_analysis_routes(app: FastAPI) -> None:
    manager = AnalysisJobManager()

    @app.post("/analysis/start")
    def start_analysis(
        request: AnalysisStartRequest,
        _: dict[str, Any] = Depends(_require_data_user),
    ) -> dict[str, str]:
        task = manager.start(request)
        return {"analysis_id": task.task_id, "status": task.status}

    @app.get("/analysis/{analysis_id}/events")
    def analysis_events(analysis_id: str, _: dict[str, Any] = Depends(_require_data_user)):
        task = manager.get(analysis_id)
        return StreamingResponse(_stream_events(task), media_type="text/event-stream")

    @app.get("/analysis/{analysis_id}/report")
    def analysis_report(analysis_id: str, _: dict[str, Any] = Depends(_require_data_user)) -> dict[str, Any]:
        task = manager.get(analysis_id)
        if task.status == "failed":
            raise HTTPException(status_code=500, detail=task.error or "分析失败。")
        return {"status": task.status, "report": task.report, "report_id": task.report_id}

    @app.get("/reports")
    def report_history(_: dict[str, Any] = Depends(_require_data_user)) -> dict[str, Any]:
        return {"items": _list_report_history()}

    @app.get("/reports/{report_id}")
    def saved_report(report_id: str, _: dict[str, Any] = Depends(_require_data_user)) -> dict[str, Any]:
        return _read_report_history(report_id)
