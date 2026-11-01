from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import pandas as pd
import requests


class ChinaStockError(RuntimeError):
    """Raised when a China stock provider cannot satisfy a request."""


_PriceCacheKey = tuple[str, str, str, str]
_PRICE_CACHE: dict[_PriceCacheKey, pd.DataFrame] = {}
_STATUS_CALLBACK: Callable[[str], None] | None = None


def clear_china_stock_price_cache() -> None:
    _PRICE_CACHE.clear()


def set_china_stock_status_callback(callback: Callable[[str], None] | None) -> None:
    global _STATUS_CALLBACK
    _STATUS_CALLBACK = callback


def _emit_status(message: str) -> None:
    if _STATUS_CALLBACK is None:
        return
    try:
        _STATUS_CALLBACK(message)
    except Exception:
        pass


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def format_user_facing_error(exc: Exception | None) -> str:
    if exc is None:
        return "没有更多错误信息"

    text = str(exc).strip()
    lowered = text.lower()
    messages: list[str] = []

    if any(part in lowered for part in ["remote end closed", "remotedisconnected", "connection aborted"]):
        messages.append("远端数据服务主动断开连接")
    if any(part in lowered for part in ["no such file", "filenotfounderror", "[errno 2]"]):
        if "akshare" in lowered or "calendar" in lowered:
            messages.append("本地数据源组件缺少必要的数据文件")
        else:
            messages.append("本地组件缺少必要文件")
    if "native library or dependency not available" in lowered or "mini_racer" in lowered:
        messages.append("本地解析组件缺少必要的依赖文件")
    if "timed out" in lowered or "timeout" in lowered:
        messages.append("请求超时")
    if "connection refused" in lowered or "failed to establish a new connection" in lowered:
        messages.append("无法连接到服务")
    if "invalid api key" in lowered or "unauthorized" in lowered or "401" in lowered:
        messages.append("鉴权失败，请检查账号、授权或 API Key")
    if "invalid credentials" in lowered or "incorrect username" in lowered or "incorrect password" in lowered:
        messages.append("账号或密码不正确")
    if "rate limit" in lowered or "too many requests" in lowered or "429" in lowered:
        messages.append("请求过于频繁或额度不足，请稍后重试")
    if "model" in lowered and "not found" in lowered:
        messages.append("模型名称不存在或当前账号无权限")

    if messages:
        return "；".join(dict.fromkeys(messages))
    if text and _contains_chinese(text):
        return text
    return "请求处理失败，请稍后重试或检查配置。"


def normalize_cn_stock_symbol(symbol: str) -> str:
    """Normalize a mainland China A-share ticker to CODE.EXCHANGE."""
    raw = symbol.strip().upper()
    if "." in raw:
        code, suffix = raw.split(".", 1)
        if suffix not in {"SH", "SZ", "BJ"} or not code.isdigit() or len(code) > 6:
            raise ChinaStockError(f"无法识别 A 股代码: {symbol}")
        return f"{code.zfill(6)}.{suffix}"

    if not raw.isdigit() or len(raw) > 6:
        raise ChinaStockError(f"无法识别 A 股代码: {symbol}")
    code = raw.zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    raise ChinaStockError(f"无法识别 A 股代码: {symbol}")


def is_cn_stock_symbol(symbol: str) -> bool:
    try:
        normalize_cn_stock_symbol(symbol)
        return True
    except ChinaStockError:
        return False


def _code(symbol: str) -> str:
    return normalize_cn_stock_symbol(symbol).split(".", 1)[0]


def _akshare_daily_symbol(symbol: str) -> str:
    code, suffix = normalize_cn_stock_symbol(symbol).split(".", 1)
    return f"{suffix.lower()}{code}"


def _market_name(symbol: str) -> str:
    suffix = normalize_cn_stock_symbol(symbol).split(".", 1)[1]
    return {
        "SH": "上海证券交易所",
        "SZ": "深圳证券交易所",
        "BJ": "北京证券交易所",
    }.get(suffix, suffix)


def _ts_code(symbol: str) -> str:
    normalized = normalize_cn_stock_symbol(symbol)
    code, suffix = normalized.split(".", 1)
    return f"{code}.{suffix}"


def _baostock_code(symbol: str) -> str:
    code, suffix = normalize_cn_stock_symbol(symbol).split(".", 1)
    return f"{suffix.lower()}.{code}"


def _symbol_from_baostock_code(symbol: str) -> str:
    raw = symbol.strip().lower()
    if "." not in raw:
        return normalize_cn_stock_symbol(raw)
    prefix, code = raw.split(".", 1)
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix)
    if not suffix:
        raise ChinaStockError(f"无法识别 BaoStock 股票代码: {symbol}")
    return normalize_cn_stock_symbol(f"{code}.{suffix}")


def _date_for_akshare(date_text: str) -> str:
    return datetime.strptime(date_text, "%Y-%m-%d").strftime("%Y%m%d")


def _date_for_output(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def _finalize_price_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "PctChange", "Turnover"] if c in df.columns]
    df = df[keep].copy()
    df["Date"] = _date_for_output(df["Date"])
    for column in ["Open", "High", "Low", "Close", "Volume", "Amount", "PctChange", "Turnover"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _format_dataframe(title: str, df: pd.DataFrame) -> str:
    if df.empty:
        return f"# {title}\n\n未找到可用数据。"
    csv_string = df.to_csv(index=False)
    return (
        f"# {title}\n"
        f"# 记录数: {len(df)}\n"
        f"# 数据获取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{csv_string}"
    )


def _result_to_dataframe(result: Any) -> pd.DataFrame:
    if getattr(result, "error_code", "0") != "0":
        message = getattr(result, "error_msg", "") or "数据源返回错误"
        raise ChinaStockError(format_user_facing_error(RuntimeError(str(message))))

    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=list(result.fields))


def _temporary_unavailable_report(title: str, data_name: str, exc: Exception) -> str:
    return (
        f"# {title}\n\n"
        f"暂时无法获取{data_name}数据。数据源连接失败，分析时请降低该项权重，并结合其它已获取的信息判断。\n\n"
        f"- 数据源: AkShare\n"
        f"- 错误: {format_user_facing_error(exc)}"
    )


@dataclass
class ChinaStockProvider:
    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        raise NotImplementedError

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise NotImplementedError

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        raise NotImplementedError

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError


class AkShareChinaStockProvider(ChinaStockProvider):
    def _akshare(self) -> Any:
        try:
            import akshare as ak
        except ImportError as exc:
            raise ChinaStockError("缺少 akshare，请先安装 akshare 后再使用 A 股默认数据源。") from exc
        return ak

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        ak = self._akshare()
        df = ak.stock_info_a_code_name()
        if df.empty:
            return []

        df = df.rename(columns={"代码": "code", "名称": "name"})
        query_lower = query.strip().lower()
        matched = df[
            df["code"].astype(str).str.contains(query_lower, case=False, na=False)
            | df["name"].astype(str).str.lower().str.contains(query_lower, na=False)
        ].head(limit)

        return [
            {
                "symbol": normalize_cn_stock_symbol(str(row["code"])),
                "code": str(row["code"]).zfill(6),
                "name": str(row["name"]),
                "market": _market_name(str(row["code"])),
            }
            for _, row in matched.iterrows()
        ]

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        ak = self._akshare()
        normalized = normalize_cn_stock_symbol(symbol)
        last_error: Exception | None = None
        for _ in range(3):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=_code(symbol),
                    period="daily",
                    start_date=_date_for_akshare(start_date),
                    end_date=_date_for_akshare(end_date),
                    adjust="qfq",
                )
                break
            except Exception as exc:
                last_error = exc
        else:
            try:
                df = ak.stock_zh_a_daily(
                    symbol=_akshare_daily_symbol(symbol),
                    start_date=_date_for_akshare(start_date),
                    end_date=_date_for_akshare(end_date),
                    adjust="qfq",
                )
            except Exception as fallback_error:
                raise ChinaStockError(
                    f"AkShare 获取行情数据失败: {normalized}，{start_date} 至 {end_date}。"
                    f"可能是数据源临时断连、限流或非交易日维护，请稍后重试或切换 Tushare。"
                    f"主接口错误: {format_user_facing_error(last_error)}；"
                    f"备用接口错误: {format_user_facing_error(fallback_error)}"
                ) from fallback_error
        if df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "日期": "Date",
                "开盘": "Open",
                "最高": "High",
                "最低": "Low",
                "收盘": "Close",
                "成交量": "Volume",
                "成交额": "Amount",
                "振幅": "Amplitude",
                "涨跌幅": "PctChange",
                "涨跌额": "Change",
                "换手率": "Turnover",
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "amount": "Amount",
                "turnover": "Turnover",
            }
        )
        keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "PctChange", "Turnover"] if c in df.columns]
        df = df[keep].copy()
        df["Date"] = _date_for_output(df["Date"])
        return df

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        ak = self._akshare()
        title = f"A股基本面: {normalize_cn_stock_symbol(symbol)}"
        try:
            df = ak.stock_individual_info_em(symbol=_code(symbol))
        except Exception as exc:
            return _temporary_unavailable_report(title, "基本面", exc)
        if df.empty:
            return f"# {title}\n\n未找到基本面数据。"
        return _format_dataframe(title, df)

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        ak = self._akshare()
        title = f"A股新闻: {normalize_cn_stock_symbol(symbol)}"
        try:
            df = ak.stock_news_em(symbol=_code(symbol))
        except Exception as exc:
            return _temporary_unavailable_report(title, "新闻", exc)
        if df.empty:
            return f"# {title}\n\n未找到相关新闻。"
        return _format_dataframe(title, df.head(20))


@dataclass
class TushareChinaStockProvider(ChinaStockProvider):
    token: str | None = None

    def _pro(self) -> Any:
        try:
            import tushare as ts
        except ImportError as exc:
            raise ChinaStockError("缺少 tushare，请先安装 tushare 后再使用 Tushare 数据源。") from exc
        if not self.token:
            raise ChinaStockError("使用 Tushare 数据源需要配置 TUSHARE_TOKEN。")
        ts.set_token(self.token)
        return ts.pro_api()

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        pro = self._pro()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,market")
        query_lower = query.strip().lower()
        matched = df[
            df["symbol"].astype(str).str.contains(query_lower, case=False, na=False)
            | df["name"].astype(str).str.lower().str.contains(query_lower, na=False)
        ].head(limit)
        return [
            {
                "symbol": normalize_cn_stock_symbol(str(row["ts_code"])),
                "code": str(row["symbol"]).zfill(6),
                "name": str(row["name"]),
                "market": str(row.get("market") or _market_name(str(row["ts_code"]))),
            }
            for _, row in matched.iterrows()
        ]

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        pro = self._pro()
        df = pro.daily(ts_code=_ts_code(symbol), start_date=_date_for_akshare(start_date), end_date=_date_for_akshare(end_date))
        if df.empty:
            return pd.DataFrame()
        df = df.rename(
            columns={
                "trade_date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "vol": "Volume",
                "amount": "Amount",
                "pct_chg": "PctChange",
            }
        )
        keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "PctChange"] if c in df.columns]
        df = df[keep].copy()
        df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
        return df.sort_values("Date")

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        pro = self._pro()
        df = pro.fina_indicator(ts_code=_ts_code(symbol), limit=8)
        return _format_dataframe(f"A股财务指标: {normalize_cn_stock_symbol(symbol)}", df)

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        return f"# A股新闻: {normalize_cn_stock_symbol(symbol)}\n\nTushare 新闻接口未在首版启用，请切换 AkShare 新闻源。"


class BaoStockChinaStockProvider(ChinaStockProvider):
    def _baostock(self) -> Any:
        try:
            import baostock as bs
        except ImportError as exc:
            raise ChinaStockError("缺少 baostock，请先安装 baostock 后再使用 BaoStock 数据源。") from exc
        return bs

    def _with_session(self, callback):
        bs = self._baostock()
        login_result = bs.login()
        if getattr(login_result, "error_code", "0") != "0":
            message = getattr(login_result, "error_msg", "") or "登录 BaoStock 失败"
            raise ChinaStockError(f"BaoStock 连接失败: {format_user_facing_error(RuntimeError(str(message)))}")
        try:
            return callback(bs)
        finally:
            bs.logout()

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        cleaned = query.strip()
        if not cleaned:
            return []

        def fetch(bs):
            if is_cn_stock_symbol(cleaned):
                return _result_to_dataframe(bs.query_stock_basic(code=_baostock_code(cleaned)))
            return _result_to_dataframe(bs.query_stock_basic(code_name=cleaned))

        df = self._with_session(fetch)
        if df.empty:
            return []

        df = df.rename(columns={"code": "code", "code_name": "name"})
        query_lower = cleaned.lower()
        matched = df[
            df["code"].astype(str).str.lower().str.contains(query_lower, na=False)
            | df["name"].astype(str).str.lower().str.contains(query_lower, na=False)
        ].head(limit)
        return [
            {
                "symbol": _symbol_from_baostock_code(str(row["code"])),
                "code": _symbol_from_baostock_code(str(row["code"])).split(".", 1)[0],
                "name": str(row["name"]),
                "market": _market_name(_symbol_from_baostock_code(str(row["code"]))),
            }
            for _, row in matched.iterrows()
        ]

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        normalized = normalize_cn_stock_symbol(symbol)

        def fetch(bs):
            return _result_to_dataframe(
                bs.query_history_k_data_plus(
                    _baostock_code(normalized),
                    "date,code,open,high,low,close,volume,amount,pctChg,turn",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
            )

        df = self._with_session(fetch)
        if df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "amount": "Amount",
                "pctChg": "PctChange",
                "turn": "Turnover",
            }
        )
        keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "PctChange", "Turnover"] if c in df.columns]
        df = df[keep].copy()
        df["Date"] = _date_for_output(df["Date"])
        for column in ["Open", "High", "Low", "Close", "Volume", "Amount", "PctChange", "Turnover"]:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")
        return df

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        return (
            f"# A股基本面: {normalize_cn_stock_symbol(symbol)}\n\n"
            "BaoStock 免费数据源当前主要用于历史行情兜底；基本面请切换 AkShare 或 Tushare。"
        )

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        return (
            f"# A股新闻: {normalize_cn_stock_symbol(symbol)}\n\n"
            "BaoStock 不提供新闻数据；新闻分析请切换 AkShare。"
        )


class FinShareChinaStockProvider(ChinaStockProvider):
    def _finshare(self) -> Any:
        try:
            import finshare as fs
        except ImportError as exc:
            raise ChinaStockError("缺少 finshare，当前隐藏备用数据源不可用。") from exc
        return fs

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        cleaned = query.strip()
        if not is_cn_stock_symbol(cleaned):
            return []
        normalized = normalize_cn_stock_symbol(cleaned)
        return [{"symbol": normalized, "code": _code(normalized), "name": normalized, "market": _market_name(normalized)}]

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        fs = self._finshare()
        normalized = normalize_cn_stock_symbol(symbol)
        try:
            df = fs.get_historical_data(normalized, start=start_date, end=end_date)
        except TypeError:
            df = fs.get_historical_data(symbol=normalized, start_date=start_date, end_date=end_date)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(
            columns={
                "trade_date": "Date",
                "date": "Date",
                "日期": "Date",
                "open_price": "Open",
                "open": "Open",
                "开盘": "Open",
                "high_price": "High",
                "high": "High",
                "最高": "High",
                "low_price": "Low",
                "low": "Low",
                "最低": "Low",
                "close_price": "Close",
                "close": "Close",
                "收盘": "Close",
                "volume": "Volume",
                "vol": "Volume",
                "成交量": "Volume",
                "amount": "Amount",
                "成交额": "Amount",
                "change_pct": "PctChange",
                "pct_chg": "PctChange",
                "turnover": "Turnover",
            }
        )
        return _finalize_price_dataframe(df)

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        return (
            f"# A股基本面: {normalize_cn_stock_symbol(symbol)}\n\n"
            "finshare 当前仅作为历史行情隐藏备用源；基本面请使用 AkShare 或 Tushare。"
        )

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        return (
            f"# A股新闻: {normalize_cn_stock_symbol(symbol)}\n\n"
            "finshare 当前仅作为历史行情隐藏备用源；新闻分析请使用 AkShare。"
        )


class ADataChinaStockProvider(ChinaStockProvider):
    def _adata(self) -> Any:
        try:
            import adata
        except ImportError as exc:
            raise ChinaStockError("缺少 adata，当前隐藏备用数据源不可用。") from exc
        return adata

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        cleaned = query.strip()
        adata = self._adata()
        if not cleaned:
            return []
        df = adata.stock.info.all_code()
        if df.empty:
            return []
        query_lower = cleaned.lower()
        matched = df[
            df["stock_code"].astype(str).str.contains(query_lower, case=False, na=False)
            | df["short_name"].astype(str).str.lower().str.contains(query_lower, na=False)
        ].head(limit)
        return [
            {
                "symbol": normalize_cn_stock_symbol(f"{row['stock_code']}.{row['exchange']}"),
                "code": str(row["stock_code"]).zfill(6),
                "name": str(row["short_name"]),
                "market": _market_name(f"{row['stock_code']}.{row['exchange']}"),
            }
            for _, row in matched.iterrows()
        ]

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        adata = self._adata()
        df = adata.stock.market.get_market(
            stock_code=_code(symbol),
            start_date=start_date,
            end_date=end_date,
            k_type=1,
            adjust_type=1,
        )
        if df.empty:
            return pd.DataFrame()
        df = df.rename(
            columns={
                "trade_date": "Date",
                "trade_time": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "amount": "Amount",
                "change_pct": "PctChange",
                "turnover": "Turnover",
            }
        )
        return _finalize_price_dataframe(df)

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        return (
            f"# A股基本面: {normalize_cn_stock_symbol(symbol)}\n\n"
            "AData 当前仅作为历史行情隐藏备用源；基本面请使用 AkShare 或 Tushare。"
        )

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        return (
            f"# A股新闻: {normalize_cn_stock_symbol(symbol)}\n\n"
            "AData 当前仅作为历史行情隐藏备用源；新闻分析请使用 AkShare。"
        )


@dataclass
class ReservedEnterpriseChinaStockProvider(ChinaStockProvider):
    display_name: str

    def _unavailable(self) -> None:
        raise ChinaStockError(
            f"{self.display_name} 企业数据源接口已预留，但当前版本尚未接入授权 SDK 或内部数据网关。"
        )

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        self._unavailable()

    def get_price_dataframe(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        self._unavailable()

    def get_fundamentals(self, symbol: str, curr_date: str | None = None) -> str:
        self._unavailable()

    def get_news(self, symbol: str, start_date: str, end_date: str) -> str:
        self._unavailable()


def get_china_stock_provider(name: str | None = None) -> ChinaStockProvider:
    from os import environ

    provider = (name or "akshare").lower()
    if provider == "akshare":
        return AkShareChinaStockProvider()
    if provider == "tushare":
        return TushareChinaStockProvider(token=environ.get("TUSHARE_TOKEN"))
    if provider == "baostock":
        return BaoStockChinaStockProvider()
    if provider == "finshare":
        return FinShareChinaStockProvider()
    if provider == "adata":
        return ADataChinaStockProvider()
    if provider == "choice":
        return ReservedEnterpriseChinaStockProvider("Choice")
    if provider == "ifind":
        return ReservedEnterpriseChinaStockProvider("iFinD")
    raise ChinaStockError(f"未知 A 股数据源: {name}")


def _configured_cn_provider(provider_name: str | None = None) -> ChinaStockProvider:
    return get_china_stock_provider(_configured_cn_provider_name(provider_name))


def _configured_cn_provider_name(provider_name: str | None = None) -> str:
    from aitrade.dataflows.config import get_config

    if provider_name:
        return provider_name.lower()
    config = get_config()
    return str(config.get("data_vendors", {}).get("cn_stock", "akshare") or "akshare").lower()


def _cached_price_dataframe(provider_name: str, symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    key = (provider_name.lower(), normalize_cn_stock_symbol(symbol), start_date, end_date)
    cached = _PRICE_CACHE.get(key)
    return cached.copy() if cached is not None else None


def _store_price_dataframe(provider_name: str, symbol: str, start_date: str, end_date: str, df: pd.DataFrame) -> pd.DataFrame:
    key = (provider_name.lower(), normalize_cn_stock_symbol(symbol), start_date, end_date)
    _PRICE_CACHE[key] = df.copy()
    return df.copy()


def _remote_gateway_base_url_and_token() -> tuple[str, str] | None:
    from aitrade.dataflows.config import get_config

    config = get_config()
    if config.get("data_gateway_enabled") is False:
        return None
    token = config.get("auth_token")
    if not isinstance(token, str) or not token.strip():
        return None
    base_url = config.get("data_gateway_url") or config.get("auth_server_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    cleaned = base_url.strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        return None
    return cleaned, token.strip()


def _remote_gateway_get(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    target = _remote_gateway_base_url_and_token()
    if target is None:
        return None
    base_url, token = target
    try:
        response = requests.get(
            f"{base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _remote_price_dataframe(symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    cached = _cached_price_dataframe("data_gateway", symbol, start_date, end_date)
    if cached is not None:
        return cached
    normalized = normalize_cn_stock_symbol(symbol)
    payload = _remote_gateway_get(
        "/data/cn/prices",
        {"symbol": normalized, "start_date": start_date, "end_date": end_date},
    )
    if payload is None:
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    df = _finalize_price_dataframe(pd.DataFrame(records))
    return _store_price_dataframe("data_gateway", normalized, start_date, end_date, df)


def _remote_stock_search(query: str, limit: int) -> list[dict[str, str]] | None:
    payload = _remote_gateway_get("/data/stocks/search", {"q": query, "limit": limit})
    if payload is None:
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    normalized_items: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        code = item.get("code")
        name = item.get("name")
        market = item.get("market")
        if all(isinstance(value, str) for value in [symbol, code, name, market]):
            normalized_items.append({"symbol": symbol, "code": code, "name": name, "market": market})
    return normalized_items


def _get_price_dataframe_from_provider(provider_name: str, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    cached = _cached_price_dataframe(provider_name, symbol, start_date, end_date)
    if cached is not None:
        return cached
    provider = get_china_stock_provider(provider_name)
    df = provider.get_price_dataframe(symbol, start_date, end_date)
    return _store_price_dataframe(provider_name, symbol, start_date, end_date, df)


def _free_price_provider_chain(provider_name: str) -> list[str]:
    free_providers = ["akshare", "baostock", "finshare", "adata"]
    if provider_name not in free_providers:
        return [provider_name]
    return [provider_name, *(provider for provider in free_providers if provider != provider_name)]


def _combined_free_price_error(normalized: str, start_date: str, end_date: str) -> ChinaStockError:
    return ChinaStockError(
        f"A 股行情数据暂时不可用: {normalized}，{start_date} 至 {end_date}。"
        "已自动重试多个免费数据源。"
        "可能是免费数据源临时断连、限流或维护，请稍后重试，或切换 Tushare。"
    )


def _configured_price_dataframe(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    normalized = normalize_cn_stock_symbol(symbol)
    remote_df = _remote_price_dataframe(normalized, start_date, end_date)
    if remote_df is not None:
        return remote_df
    provider_name = _configured_cn_provider_name()
    if provider_name == "tushare":
        return _get_price_dataframe_from_provider(provider_name, normalized, start_date, end_date)

    last_error: Exception | None = None
    for fallback_provider in _free_price_provider_chain(provider_name):
        try:
            return _get_price_dataframe_from_provider(fallback_provider, normalized, start_date, end_date)
        except Exception as exc:
            last_error = exc
    raise _combined_free_price_error(normalized, start_date, end_date) from last_error


def search_cn_stocks(query: str, vendor_name: str | None = None, limit: int = 20) -> list[dict[str, str]]:
    if vendor_name is None:
        remote_items = _remote_stock_search(query, limit)
        if remote_items is not None:
            return remote_items
    return _configured_cn_provider(vendor_name).search_stocks(query, limit=limit)


def get_china_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    if not is_cn_stock_symbol(symbol):
        from aitrade.dataflows.y_finance import get_YFin_data_online

        return get_YFin_data_online(symbol, start_date, end_date)
    normalized = normalize_cn_stock_symbol(symbol)
    df = _configured_price_dataframe(normalized, start_date, end_date)
    return _format_dataframe(f"A股行情数据: {normalized}", df)


def get_china_stock_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    if not is_cn_stock_symbol(symbol):
        from aitrade.dataflows.y_finance import get_stock_stats_indicators_window

        return get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days)

    from dateutil.relativedelta import relativedelta
    from stockstats import wrap

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - relativedelta(days=look_back_days + 120)
    df = _configured_price_dataframe(
        symbol,
        start_dt.strftime("%Y-%m-%d"),
        curr_date,
    )
    if df.empty:
        return f"## {indicator}\n\n未找到 {normalize_cn_stock_symbol(symbol)} 的指标数据。"

    stock_df = wrap(df.copy())
    stock_df[indicator]
    stock_df["Date"] = pd.to_datetime(stock_df["Date"]).dt.strftime("%Y-%m-%d")
    cutoff = end_dt - relativedelta(days=look_back_days)
    window = stock_df[pd.to_datetime(stock_df["Date"]) >= cutoff]
    lines = [f"{row['Date']}: {row[indicator]}" for _, row in window.iterrows()]
    return f"## {indicator} values for {normalize_cn_stock_symbol(symbol)}\n\n" + "\n".join(lines)


def get_china_stock_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    if not is_cn_stock_symbol(ticker):
        from aitrade.dataflows.y_finance import get_fundamentals

        return get_fundamentals(ticker, curr_date)
    return _configured_cn_provider().get_fundamentals(ticker, curr_date)


def get_china_stock_statement(ticker: str, statement: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    if not is_cn_stock_symbol(ticker):
        from aitrade.dataflows import y_finance

        fallback = {
            "balance_sheet": y_finance.get_balance_sheet,
            "cashflow": y_finance.get_cashflow,
            "income_statement": y_finance.get_income_statement,
        }[statement]
        return fallback(ticker, freq, curr_date)
    return (
        f"# A股财务报表: {normalize_cn_stock_symbol(ticker)}\n\n"
        f"首版已启用 A 股基本面摘要。详细 {statement} 报表将在后续版本接入。"
    )


def get_china_stock_news(ticker: str, start_date: str, end_date: str) -> str:
    if not is_cn_stock_symbol(ticker):
        from aitrade.dataflows.yfinance_news import get_news_yfinance

        return get_news_yfinance(ticker, start_date, end_date)
    return _configured_cn_provider().get_news(ticker, start_date, end_date)


def get_china_global_news(curr_date: str, look_back_days: int | None = None, limit: int | None = None) -> str:
    from aitrade.dataflows.config import get_config

    config = get_config()
    lookback = look_back_days or int(config.get("global_news_lookback_days") or 7)
    return (
        "# A 股宏观与市场环境\n\n"
        f"- 截止日期: {curr_date}\n"
        f"- 观察窗口: 最近 {lookback} 天\n\n"
        "当前 A 股版本优先使用国内个股新闻、公告相关信息、东方财富情绪与服务器公开数据缓存。"
        "外部宏观新闻源暂未启用；分析时请将本段视为宏观背景数据不足提示，"
        "不要编造未提供的宏观事件。"
    )


def get_china_insider_transactions(ticker: str) -> str:
    if not is_cn_stock_symbol(ticker):
        from aitrade.dataflows.y_finance import get_insider_transactions

        return get_insider_transactions(ticker)
    return f"# A股内部交易: {normalize_cn_stock_symbol(ticker)}\n\nA股首版不提供美股式 insider transactions 数据。"
