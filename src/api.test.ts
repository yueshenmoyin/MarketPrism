import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildStockSearchPath,
  getReport,
  getCompletedReportIfReady,
  getSavedReport,
  listReports,
  localizeReportText,
  login,
  shouldShowAdvancedDataControls,
  shouldShowAdvancedModelControls,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("buildStockSearchPath", () => {
  it("includes the selected market when searching stocks", () => {
    expect(buildStockSearchPath("AAPL", "us_stock")).toBe("/stocks/search?q=AAPL&market=us_stock");
    expect(buildStockSearchPath("0700", "hk_stock")).toBe("/stocks/search?q=0700&market=hk_stock");
  });

  it("includes the selected A share data vendor when provided", () => {
    expect(buildStockSearchPath("茅台", "cn_a_share", "baostock")).toBe(
      "/stocks/search?q=%E8%8C%85%E5%8F%B0&market=cn_a_share&vendor=baostock",
    );
  });

  it("posts only login credentials to the local auth endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ authenticated: true, user: { name: "测试用户" }, expires_at: "2099-01-01T00:00:00Z" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await login({
      username: "demo",
      password: "secret",
    });

    expect(result.authenticated).toBe(true);
    expect(result.expires_at).toBe("2099-01-01T00:00:00Z");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          username: "demo",
          password: "secret",
        }),
      }),
    );
  });

  it("loads report history and saved report content", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "report.md", title: "贵州茅台", ticker: "600519.SH", analysis_date: "2026-05-24" }] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "report.md", title: "贵州茅台", report: "# 报告" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const history = await listReports();
    const report = await getSavedReport("report.md");

    expect(history.items[0].title).toBe("贵州茅台");
    expect(report.report).toBe("# 报告");
    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8765/reports", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:8765/reports/report.md", expect.any(Object));
  });

  it("localizes English structured report labels before display", async () => {
    const englishReport = [
      "## 组合经理决策",
      "",
      "**Rating**: Hold",
      "",
      "**Executive Summary**: 维持观察。",
      "",
      "**Investment Thesis**: 基本面稳健。",
      "",
      "**Price Target**: 1362.0",
      "",
      "**Time Horizon**: 1-3个月",
    ].join("\n");

    expect(localizeReportText(englishReport)).toContain("**评级**：持有");
    expect(localizeReportText(englishReport)).toContain("**执行摘要**：维持观察。");
    expect(localizeReportText(englishReport)).toContain("**投资逻辑**：基本面稳健。");
    expect(localizeReportText(englishReport)).not.toContain("Executive Summary");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "completed", report: englishReport }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "report.md", title: "旧报告", report: englishReport }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const current = await getReport("task-id");
    const saved = await getSavedReport("report.md");

    expect(current.report).toContain("**评级**：持有");
    expect(saved.report).toContain("**执行摘要**：维持观察。");
  });

  it("returns a completed report for stream-error recovery only when the report is ready", async () => {
    const completed = await getCompletedReportIfReady("done-task", async () => ({
      status: "completed",
      report: "# 已完成报告",
      report_id: "done.md",
    }));
    const stillRunning = await getCompletedReportIfReady("running-task", async () => ({
      status: "running",
      report: null,
    }));

    expect(completed?.report).toBe("# 已完成报告");
    expect(completed?.report_id).toBe("done.md");
    expect(stillRunning).toBeNull();
  });
});

describe("A 股普通客户模式", () => {
  it("默认隐藏数据源配置", () => {
    expect(shouldShowAdvancedDataControls({ primary_market: "cn_a_share", advanced_mode: false })).toBe(false);
  });

  it("高级模式才显示数据源配置", () => {
    expect(shouldShowAdvancedDataControls({ primary_market: "cn_a_share", advanced_mode: true })).toBe(true);
  });

  it("默认隐藏模型和 API Key 配置", () => {
    expect(shouldShowAdvancedModelControls({ advanced_mode: false })).toBe(false);
    expect(shouldShowAdvancedModelControls({})).toBe(false);
  });

  it("高级模式才显示模型和 API Key 配置", () => {
    expect(shouldShowAdvancedModelControls({ advanced_mode: true })).toBe(true);
  });
});
