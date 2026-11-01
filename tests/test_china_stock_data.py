import sys
import types
import unittest
from copy import deepcopy

import pandas as pd
import pytest

from aitrade.default_config import DEFAULT_CONFIG
from aitrade.dataflows.config import set_config


@pytest.fixture(autouse=True)
def _reset_dataflow_config():
    import aitrade.dataflows.config as dataflow_config

    dataflow_config._config = deepcopy(DEFAULT_CONFIG)
    yield
    dataflow_config._config = deepcopy(DEFAULT_CONFIG)


@pytest.mark.unit
class ChinaStockSymbolTests(unittest.TestCase):
    def test_normalize_cn_stock_symbol_adds_exchange_suffix(self):
        from aitrade.dataflows.china_stock import normalize_cn_stock_symbol

        self.assertEqual(normalize_cn_stock_symbol("600519"), "600519.SH")
        self.assertEqual(normalize_cn_stock_symbol("000001"), "000001.SZ")
        self.assertEqual(normalize_cn_stock_symbol("300750"), "300750.SZ")
        self.assertEqual(normalize_cn_stock_symbol("430047"), "430047.BJ")

    def test_normalize_cn_stock_symbol_preserves_existing_suffix(self):
        from aitrade.dataflows.china_stock import normalize_cn_stock_symbol

        self.assertEqual(normalize_cn_stock_symbol(" 600519.sh "), "600519.SH")
        self.assertEqual(normalize_cn_stock_symbol("000001.SZ"), "000001.SZ")

    def test_non_a_share_ticker_is_not_treated_as_china_stock(self):
        from aitrade.dataflows.china_stock import ChinaStockError, is_cn_stock_symbol, normalize_cn_stock_symbol

        self.assertFalse(is_cn_stock_symbol("AAPL"))
        self.assertFalse(is_cn_stock_symbol("0700.HK"))
        self.assertFalse(is_cn_stock_symbol("200001"))
        self.assertFalse(is_cn_stock_symbol("900901"))
        with self.assertRaises(ChinaStockError):
            normalize_cn_stock_symbol("AAPL")
        with self.assertRaises(ChinaStockError):
            normalize_cn_stock_symbol("200001")


@pytest.mark.unit
def test_akshare_provider_returns_unified_stock_search(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider

    fake_akshare = types.SimpleNamespace(
        stock_info_a_code_name=lambda: pd.DataFrame(
            [
                {"code": "600519", "name": "贵州茅台"},
                {"code": "000001", "name": "平安银行"},
            ]
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = AkShareChinaStockProvider().search_stocks("茅台")

    assert result == [
        {
            "symbol": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "market": "上海证券交易所",
        }
    ]


@pytest.mark.unit
def test_china_stock_data_routes_to_configured_akshare_provider(monkeypatch):
    from aitrade.dataflows.interface import route_to_vendor

    fake_akshare = types.SimpleNamespace(
        stock_zh_a_hist=lambda symbol, period, start_date, end_date, adjust: pd.DataFrame(
            [
                {
                    "日期": "2026-05-18",
                    "开盘": 1680.1,
                    "收盘": 1695.2,
                    "最高": 1700.0,
                    "最低": 1678.0,
                    "成交量": 32100,
                }
            ]
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"}})
    set_config({"tool_vendors": {"get_stock_data": "china_stock"}})

    data = route_to_vendor("get_stock_data", "600519", "2026-05-18", "2026-05-19")

    assert "# A股行情数据: 600519.SH" in data
    assert "2026-05-18" in data
    assert "1695.2" in data


@pytest.mark.unit
def test_akshare_price_remote_disconnect_raises_chinese_context(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider, ChinaStockError

    def raise_disconnect(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")

    def raise_native_dependency_error(symbol, start_date, end_date, adjust):
        raise RuntimeError(r"Native library or dependency not available at D:\Programs\AITrade A-Share\aitrade-backend\_internal\py_mini_racer\mini_racer.dll")

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_disconnect, stock_zh_a_daily=raise_native_dependency_error)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    with pytest.raises(ChinaStockError) as caught:
        AkShareChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert "AkShare 获取行情数据失败" in str(caught.value)
    assert "600519.SH" in str(caught.value)
    assert "远端数据服务主动断开连接" in str(caught.value)
    assert "本地解析组件缺少必要的依赖文件" in str(caught.value)
    assert "RemoteDisconnected" not in str(caught.value)
    assert "Remote end closed connection" not in str(caught.value)
    assert "Native library" not in str(caught.value)
    assert "mini_racer.dll" not in str(caught.value)


@pytest.mark.unit
def test_akshare_price_remote_disconnect_retries_before_failing(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider

    calls = {"count": 0}

    def fail_once_then_return(symbol, period, start_date, end_date, adjust):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("Remote end closed connection without response")
        return pd.DataFrame(
            [
                {
                    "日期": "2026-05-18",
                    "开盘": 1680.1,
                    "收盘": 1695.2,
                    "最高": 1700.0,
                    "最低": 1678.0,
                    "成交量": 32100,
                }
            ]
        )

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=fail_once_then_return)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    data = AkShareChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert calls["count"] == 2
    assert data.iloc[0]["Close"] == 1695.2


@pytest.mark.unit
def test_akshare_price_remote_disconnect_uses_daily_fallback(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider

    def raise_disconnect(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("Remote end closed connection without response")

    def fallback_daily(symbol, start_date, end_date, adjust):
        assert symbol == "sh600519"
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-18",
                    "open": 1680.1,
                    "high": 1700.0,
                    "low": 1678.0,
                    "close": 1695.2,
                    "volume": 32100,
                    "amount": 54000000,
                    "turnover": 0.12,
                }
            ]
        )

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_disconnect, stock_zh_a_daily=fallback_daily)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    data = AkShareChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert data.iloc[0]["Close"] == 1695.2
    assert data.iloc[0]["Amount"] == 54000000


@pytest.mark.unit
def test_akshare_fundamentals_remote_disconnect_returns_recoverable_report(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider

    def raise_disconnect(symbol):
        raise ConnectionError("Remote end closed connection without response")

    fake_akshare = types.SimpleNamespace(stock_individual_info_em=raise_disconnect)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    report = AkShareChinaStockProvider().get_fundamentals("600519")

    assert "# A股基本面: 600519.SH" in report
    assert "暂时无法获取基本面数据" in report
    assert "远端数据服务主动断开连接" in report
    assert "Remote end closed connection" not in report


@pytest.mark.unit
def test_akshare_news_remote_disconnect_returns_recoverable_report(monkeypatch):
    from aitrade.dataflows.china_stock import AkShareChinaStockProvider

    def raise_disconnect(symbol):
        raise ConnectionError("Remote end closed connection without response")

    fake_akshare = types.SimpleNamespace(stock_news_em=raise_disconnect)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    report = AkShareChinaStockProvider().get_news("600519", "2026-05-22", "2026-05-23")

    assert "# A股新闻: 600519.SH" in report
    assert "暂时无法获取新闻数据" in report
    assert "远端数据服务主动断开连接" in report
    assert "Remote end closed connection" not in report


class _FakeBaoStockResult:
    def __init__(self, fields, rows, error_code="0", error_msg=""):
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = error_msg
        self._index = -1

    def next(self):
        self._index += 1
        return self._index < len(self.rows)

    def get_row_data(self):
        return self.rows[self._index]


@pytest.mark.unit
def test_baostock_provider_returns_unified_stock_search(monkeypatch):
    from aitrade.dataflows.china_stock import BaoStockChinaStockProvider

    calls = []
    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: calls.append("logout"),
        query_stock_basic=lambda code="", code_name="": _FakeBaoStockResult(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [
                ["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"],
                ["sz.000001", "平安银行", "1991-04-03", "", "1", "1"],
            ],
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)

    result = BaoStockChinaStockProvider().search_stocks("茅台")

    assert result == [
        {
            "symbol": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "market": "上海证券交易所",
        },
    ]
    assert calls == ["logout"]


@pytest.mark.unit
def test_baostock_provider_returns_unified_price_dataframe(monkeypatch):
    from aitrade.dataflows.china_stock import BaoStockChinaStockProvider

    calls = []

    def query_history_k_data_plus(code, fields, start_date, end_date, frequency, adjustflag):
        calls.append((code, fields, start_date, end_date, frequency, adjustflag))
        return _FakeBaoStockResult(
            ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "turn"],
            [["2026-05-18", "sh.600519", "1680.1", "1700.0", "1678.0", "1695.2", "32100", "54000000", "1.2", "0.12"]],
        )

    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: calls.append("logout"),
        query_history_k_data_plus=query_history_k_data_plus,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)

    data = BaoStockChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert calls[0][0] == "sh.600519"
    assert calls[0][4] == "d"
    assert calls[0][5] == "2"
    assert calls[-1] == "logout"
    assert data.iloc[0]["Date"] == "2026-05-18"
    assert data.iloc[0]["Close"] == 1695.2
    assert data.iloc[0]["Volume"] == 32100


@pytest.mark.unit
def test_finshare_provider_returns_unified_price_dataframe(monkeypatch):
    from aitrade.dataflows.china_stock import FinShareChinaStockProvider

    calls = []

    def get_historical_data(code, start, end):
        calls.append((code, start, end))
        return pd.DataFrame(
            [
                {
                    "trade_date": "2026-05-18",
                    "open_price": 1680.1,
                    "high_price": 1700.0,
                    "low_price": 1678.0,
                    "close_price": 1695.2,
                    "volume": 32100,
                    "amount": 54000000,
                }
            ]
        )

    monkeypatch.setitem(sys.modules, "finshare", types.SimpleNamespace(get_historical_data=get_historical_data))

    data = FinShareChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert calls == [("600519.SH", "2026-05-18", "2026-05-23")]
    assert data.iloc[0]["Date"] == "2026-05-18"
    assert data.iloc[0]["Close"] == 1695.2
    assert data.iloc[0]["Amount"] == 54000000


@pytest.mark.unit
def test_adata_provider_returns_unified_price_dataframe(monkeypatch):
    from aitrade.dataflows.china_stock import ADataChinaStockProvider

    calls = []

    fake_market = types.SimpleNamespace(
        get_market=lambda stock_code, start_date, end_date, k_type, adjust_type: (
            calls.append((stock_code, start_date, end_date, k_type, adjust_type))
            or pd.DataFrame(
                [
                    {
                        "trade_date": "2026-05-18",
                        "open": 1680.1,
                        "high": 1700.0,
                        "low": 1678.0,
                        "close": 1695.2,
                        "volume": 32100,
                        "amount": 54000000,
                    }
                ]
            )
        )
    )
    fake_adata = types.SimpleNamespace(stock=types.SimpleNamespace(market=fake_market))
    monkeypatch.setitem(sys.modules, "adata", fake_adata)

    data = ADataChinaStockProvider().get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    assert calls == [("600519", "2026-05-18", "2026-05-23", 1, 1)]
    assert data.iloc[0]["Date"] == "2026-05-18"
    assert data.iloc[0]["Close"] == 1695.2
    assert data.iloc[0]["Amount"] == 54000000


@pytest.mark.unit
def test_china_stock_data_routes_to_configured_baostock_provider(monkeypatch):
    from aitrade.dataflows.interface import route_to_vendor

    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, start_date, end_date, frequency, adjustflag: _FakeBaoStockResult(
            ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "turn"],
            [["2026-05-18", "sh.600519", "1680.1", "1700.0", "1678.0", "1695.2", "32100", "54000000", "1.2", "0.12"]],
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "baostock"}})
    set_config({"tool_vendors": {"get_stock_data": "china_stock"}})

    data = route_to_vendor("get_stock_data", "600519", "2026-05-18", "2026-05-19")

    assert "# A股行情数据: 600519.SH" in data
    assert "2026-05-18" in data
    assert "1695.2" in data


@pytest.mark.unit
def test_configured_akshare_price_falls_back_to_baostock(monkeypatch):
    from aitrade.dataflows.china_stock import clear_china_stock_price_cache, get_china_stock_data

    def raise_disconnect(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("RemoteDisconnected('Remote end closed connection without response')")

    def raise_native_dependency_error(symbol, start_date, end_date, adjust):
        raise RuntimeError(r"Native library or dependency not available at D:\Programs\AITrade A-Share\aitrade-backend\_internal\py_mini_racer\mini_racer.dll")

    baostock_calls = []
    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_disconnect, stock_zh_a_daily=raise_native_dependency_error)
    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, start_date, end_date, frequency, adjustflag: (
            baostock_calls.append(code)
            or _FakeBaoStockResult(
                ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "turn"],
                [["2026-05-18", "sh.600519", "1680.1", "1700.0", "1678.0", "1695.2", "32100", "54000000", "1.2", "0.12"]],
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"}})
    clear_china_stock_price_cache()

    data = get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    assert "# A股行情数据: 600519.SH" in data
    assert "1695.2" in data
    assert baostock_calls == ["sh.600519"]


@pytest.mark.unit
def test_configured_akshare_and_baostock_failures_fall_back_to_hidden_free_source_without_status_events(monkeypatch):
    from aitrade.dataflows.china_stock import (
        clear_china_stock_price_cache,
        get_china_stock_data,
        set_china_stock_status_callback,
    )

    def raise_disconnect(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("RemoteDisconnected('Remote end closed connection without response')")

    def raise_native_dependency_error(symbol, start_date, end_date, adjust):
        raise RuntimeError(r"Native library or dependency not available at D:\Programs\AITrade A-Share\aitrade-backend\_internal\py_mini_racer\mini_racer.dll")

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_disconnect, stock_zh_a_daily=raise_native_dependency_error)
    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, start_date, end_date, frequency, adjustflag: _FakeBaoStockResult(
            ["date", "code"],
            [],
            error_code="1",
            error_msg="Remote end closed connection without response",
        ),
    )
    fake_finshare = types.SimpleNamespace(
        get_historical_data=lambda code, start, end: pd.DataFrame(
            [
                {
                    "trade_date": "2026-05-18",
                    "open_price": 1680.1,
                    "high_price": 1700.0,
                    "low_price": 1678.0,
                    "close_price": 1695.2,
                    "volume": 32100,
                    "amount": 54000000,
                }
            ]
        )
    )
    status_events = []
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    monkeypatch.setitem(sys.modules, "finshare", fake_finshare)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"}})
    clear_china_stock_price_cache()
    set_china_stock_status_callback(status_events.append)

    try:
        data = get_china_stock_data("600519", "2026-05-18", "2026-05-23")
    finally:
        set_china_stock_status_callback(None)

    assert "1695.2" in data
    assert status_events == []


@pytest.mark.unit
def test_all_free_price_failures_are_user_facing(monkeypatch):
    from aitrade.dataflows.china_stock import ChinaStockError, clear_china_stock_price_cache, get_china_stock_data

    def raise_disconnect(symbol, period, start_date, end_date, adjust):
        raise ConnectionError("RemoteDisconnected('Remote end closed connection without response')")

    def raise_native_dependency_error(symbol, start_date, end_date, adjust):
        raise RuntimeError(r"Native library or dependency not available at D:\Programs\AITrade A-Share\aitrade-backend\_internal\py_mini_racer\mini_racer.dll")

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_disconnect, stock_zh_a_daily=raise_native_dependency_error)
    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, start_date, end_date, frequency, adjustflag: _FakeBaoStockResult(
            ["date", "code"],
            [],
            error_code="1",
            error_msg="Remote end closed connection without response",
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    monkeypatch.setitem(
        sys.modules,
        "finshare",
        types.SimpleNamespace(get_historical_data=lambda code, start, end: (_ for _ in ()).throw(RuntimeError("RemoteDisconnected"))),
    )
    fake_adata = types.SimpleNamespace(
        stock=types.SimpleNamespace(
            market=types.SimpleNamespace(
                get_market=lambda stock_code, start_date, end_date, k_type, adjust_type: (_ for _ in ()).throw(
                    RuntimeError("Native library or dependency not available at mini_racer.dll")
                )
            )
        )
    )
    monkeypatch.setitem(sys.modules, "adata", fake_adata)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"}})
    clear_china_stock_price_cache()

    with pytest.raises(ChinaStockError) as caught:
        get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    message = str(caught.value)
    assert "A 股行情数据暂时不可用" in message
    assert "已自动重试多个免费数据源" in message
    assert "切换 Tushare" in message
    assert "AkShare" not in message
    assert "BaoStock" not in message
    assert "finshare" not in message.lower()
    assert "adata" not in message.lower()
    assert "RemoteDisconnected" not in message
    assert "Remote end closed connection" not in message
    assert "Native library" not in message
    assert "mini_racer.dll" not in message


@pytest.mark.unit
def test_configured_price_data_reuses_process_cache(monkeypatch):
    from aitrade.dataflows.china_stock import clear_china_stock_price_cache, get_china_stock_data

    calls = {"akshare": 0}

    def stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
        calls["akshare"] += 1
        return pd.DataFrame(
            [
                {
                    "日期": "2026-05-18",
                    "开盘": 1680.1,
                    "收盘": 1695.2,
                    "最高": 1700.0,
                    "最低": 1678.0,
                    "成交量": 32100,
                }
            ]
        )

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist)
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"}})
    clear_china_stock_price_cache()

    first = get_china_stock_data("600519", "2026-05-18", "2026-05-23")
    second = get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    assert "1695.2" in first
    assert "1695.2" in second
    assert calls["akshare"] == 1


@pytest.mark.unit
def test_configured_price_data_uses_remote_gateway_when_logged_in(monkeypatch):
    from aitrade.dataflows.china_stock import clear_china_stock_price_cache, get_china_stock_data

    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "symbol": "600519.SH",
                "records": [
                    {
                        "Date": "2026-05-18",
                        "Open": 1680.1,
                        "High": 1700.0,
                        "Low": 1678.0,
                        "Close": 1695.2,
                        "Volume": 32100,
                    }
                ],
            }

        def raise_for_status(self):
            return None

    def fake_get(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return Response()

    monkeypatch.setattr("aitrade.dataflows.china_stock.requests.get", fake_get)
    set_config(
        {
            "auth_server_url": "https://api.example.com/",
            "auth_token": "server-token",
            "data_gateway_enabled": True,
            "data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "akshare"},
        }
    )
    clear_china_stock_price_cache()

    data = get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    assert "1695.2" in data
    assert calls == [
        (
            "https://api.example.com/data/cn/prices",
            {"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"},
            {"Authorization": "Bearer server-token"},
            20,
        )
    ]


@pytest.mark.unit
def test_configured_baostock_is_direct_and_does_not_call_akshare(monkeypatch):
    from aitrade.dataflows.china_stock import clear_china_stock_price_cache, get_china_stock_data

    def raise_if_called(*args, **kwargs):
        raise AssertionError("AkShare should not be called when BaoStock is selected")

    fake_akshare = types.SimpleNamespace(stock_zh_a_hist=raise_if_called, stock_zh_a_daily=raise_if_called)
    fake_baostock = types.SimpleNamespace(
        login=lambda: types.SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_history_k_data_plus=lambda code, fields, start_date, end_date, frequency, adjustflag: _FakeBaoStockResult(
            ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg", "turn"],
            [["2026-05-18", "sh.600519", "1680.1", "1700.0", "1678.0", "1695.2", "32100", "54000000", "1.2", "0.12"]],
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "baostock"}})
    clear_china_stock_price_cache()

    data = get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    assert "1695.2" in data


@pytest.mark.unit
def test_configured_tushare_does_not_fall_back_to_baostock(monkeypatch):
    from aitrade.dataflows.china_stock import ChinaStockError, clear_china_stock_price_cache, get_china_stock_data

    def raise_if_called(*args, **kwargs):
        raise AssertionError("BaoStock should not be called when Tushare is selected")

    fake_baostock = types.SimpleNamespace(login=raise_if_called)
    monkeypatch.setitem(sys.modules, "baostock", fake_baostock)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    set_config({"data_vendors": {"core_stock_apis": "china_stock", "cn_stock": "tushare"}})
    clear_china_stock_price_cache()

    with pytest.raises(ChinaStockError) as caught:
        get_china_stock_data("600519", "2026-05-18", "2026-05-23")

    assert "TUSHARE_TOKEN" in str(caught.value)


@pytest.mark.unit
@pytest.mark.parametrize("vendor_name,display_name", [("choice", "Choice"), ("ifind", "iFinD")])
def test_enterprise_china_stock_sources_are_reserved_with_chinese_error(vendor_name, display_name):
    from aitrade.dataflows.china_stock import ChinaStockError, get_china_stock_provider

    provider = get_china_stock_provider(vendor_name)

    with pytest.raises(ChinaStockError) as caught:
        provider.get_price_dataframe("600519", "2026-05-18", "2026-05-23")

    message = str(caught.value)
    assert display_name in message
    assert "企业数据源" in message
    assert "预留" in message
    assert "授权" in message


@pytest.mark.unit
def test_china_global_news_does_not_call_yahoo_for_a_share_mode(monkeypatch):
    from aitrade.dataflows.china_stock import get_china_global_news

    class BlockYahooNews:
        def __getattr__(self, name):
            raise AssertionError("A 股宏观新闻不应调用 Yahoo Finance")

    monkeypatch.setitem(sys.modules, "aitrade.dataflows.yfinance_news", BlockYahooNews())

    report = get_china_global_news("2026-06-12", look_back_days=7, limit=5)

    assert "A 股" in report
    assert "Yahoo" not in report
