import { type FormEvent, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Download,
  FileText,
  History,
  KeyRound,
  Loader2,
  LogIn,
  LogOut,
  Play,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import {
  AnalysisEvent,
  AuthSession,
  DesktopConfig,
  Market,
  ReportSummary,
  StockSearchItem,
  getSavedReport,
  getAuthSession,
  getCompletedReportIfReady,
  getConfig,
  getReport,
  listReports,
  login,
  logout,
  searchStocks,
  startAnalysis,
  subscribeAnalysis,
  updateConfig,
} from "./api";
import { AnalysisRunHint, AnalysisWarRoom, analystOptions } from "./AnalysisWarRoom";
import { waitForBackendConfig } from "./backendReady";

const marketOptions: { key: Market; label: string; hint?: string; placeholder: string; defaultQuery: string }[] = [
  { key: "cn_a_share", label: "A 股", placeholder: "贵州茅台 / 600519", defaultQuery: "贵州茅台" },
  { key: "hk_stock", label: "港股", hint: "Yahoo Finance", placeholder: "腾讯控股 / 0700", defaultQuery: "0700" },
  { key: "us_stock", label: "美股", hint: "Yahoo Finance", placeholder: "Apple / AAPL", defaultQuery: "AAPL" },
];

function today() {
  return new Date().toISOString().slice(0, 10);
}

function sessionUserLabel(session: AuthSession | null) {
  const user = session?.user;
  if (!user) return "已登录";
  return String(user.name || user.username || user.account || "已登录");
}

function licenseRemainingLabel(session: AuthSession | null) {
  if (!session?.expires_at) return "永久有效";
  const seconds = session.expires_in_seconds ?? Math.floor((new Date(session.expires_at).getTime() - Date.now()) / 1000);
  if (seconds <= 0) return "已过期";
  const days = Math.floor(seconds / 86400);
  if (days >= 1) return `剩余 ${days} 天`;
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) return `剩余 ${hours} 小时`;
  return "不足 1 小时";
}

function eventTypeLabel(type: string) {
  const labels: Record<string, string> = {
    status: "状态",
    completed: "完成",
    error: "错误",
  };
  return labels[type] || type;
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function printReportAsPdf(reportText: string, title: string) {
  const printFrame = document.createElement("iframe");
  printFrame.style.position = "fixed";
  printFrame.style.right = "0";
  printFrame.style.bottom = "0";
  printFrame.style.width = "0";
  printFrame.style.height = "0";
  printFrame.style.border = "0";
  document.body.appendChild(printFrame);
  const printDocument = printFrame.contentDocument || printFrame.contentWindow?.document;
  if (!printDocument) {
    printFrame.remove();
    return false;
  }
  const safeTitle = escapeHtml(title || "观澜 MarketPrism 分析报告");
  const safeReport = escapeHtml(reportText);
  printDocument.open();
  printDocument.write(`
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>${safeTitle}</title>
        <style>
          body { margin: 28px; color: #1f2925; font-family: "Microsoft YaHei UI", "Noto Sans SC", "PingFang SC", sans-serif; }
          pre { white-space: pre-wrap; word-break: break-word; font-family: "Microsoft YaHei UI", "Noto Sans SC", "PingFang SC", sans-serif; line-height: 1.65; font-size: 13px; }
          @page { margin: 16mm; }
        </style>
      </head>
      <body><pre>${safeReport}</pre></body>
    </html>
  `);
  printDocument.close();
  window.setTimeout(() => {
    printFrame.contentWindow?.focus();
    printFrame.contentWindow?.print();
    window.setTimeout(() => printFrame.remove(), 1000);
  }, 250);
  return true;
}

function App() {
  const [config, setConfig] = useState<DesktopConfig | null>(null);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [booting, setBooting] = useState(true);
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [query, setQuery] = useState("贵州茅台");
  const [selectedMarket, setSelectedMarket] = useState<Market>("cn_a_share");
  const [stockResults, setStockResults] = useState<StockSearchItem[]>([]);
  const [selectedStock, setSelectedStock] = useState<StockSearchItem | null>(null);
  const [analysisDate, setAnalysisDate] = useState(today());
  const [depth, setDepth] = useState(1);
  const [selectedAnalysts, setSelectedAnalysts] = useState<string[]>(["market", "fundamentals", "news"]);
  const [events, setEvents] = useState<AnalysisEvent[]>([]);
  const [report, setReport] = useState("");
  const [reportTitle, setReportTitle] = useState("中文报告");
  const [activeReportId, setActiveReportId] = useState<string | null>(null);
  const [reportHistory, setReportHistory] = useState<ReportSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      setBooting(true);
      await invoke("start_backend").catch(() => undefined);
      try {
        const payload = await waitForBackendConfig(getConfig);
        if (cancelled) return;
        setConfig(payload);
        setSelectedMarket(payload.primary_market || "cn_a_share");
        setDepth(payload.max_debate_rounds || 1);
        const auth = await getAuthSession();
        if (cancelled) return;
        setSession(auth);
      } catch (err) {
        if (!cancelled) {
          setError(`无法连接本地 API: ${err instanceof Error ? err.message : String(err)}`);
        }
      } finally {
        if (!cancelled) {
          setBooting(false);
        }
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!session?.authenticated) {
      setStockResults([]);
      return;
    }
    const handle = window.setTimeout(() => {
      if (!query.trim()) {
        setStockResults([]);
        return;
      }
      searchStocks(query, selectedMarket)
        .then((payload) => setStockResults(payload.items))
        .catch((err: Error) => setError(`股票搜索失败: ${err.message}`));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [query, selectedMarket, session?.authenticated]);

  useEffect(() => {
    if (session?.authenticated) {
      void loadReportHistory();
    } else {
      setReportHistory([]);
    }
  }, [session?.authenticated]);

  const canRun = useMemo(
    () =>
      Boolean(
        session?.authenticated &&
        selectedStock &&
        selectedAnalysts.length > 0,
      ),
    [session?.authenticated, selectedStock, selectedAnalysts],
  );

  function toggleAnalyst(key: string) {
    setSelectedAnalysts((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }

  function switchMarket(market: Market) {
    setSelectedMarket(market);
    setSelectedStock(null);
    setStockResults([]);
    setQuery(marketOptions.find((item) => item.key === market)?.defaultQuery || "");
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginBusy(true);
    setLoginError("");
    setError("");
    try {
      const nextSession = await login({
        username: loginName.trim(),
        password: loginPassword,
      });
      setSession(nextSession);
      setConfig((current) => current ? { ...current, auth_server_url: nextSession.auth_server_url } : current);
      setLoginPassword("");
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "登录失败。");
    } finally {
      setLoginBusy(false);
    }
  }

  async function handleLogout() {
    setError("");
    try {
      const nextSession = await logout();
      setSession(nextSession);
      setReport("");
      setReportTitle("中文报告");
      setReportHistory([]);
      setActiveReportId(null);
      setEvents([]);
      setSelectedStock(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "退出登录失败。");
    }
  }

  async function loadReportHistory() {
    try {
      const payload = await listReports();
      setReportHistory(payload.items);
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      if (message.includes("Not Found")) {
        setError("历史报告加载失败：检测到旧版本本地服务，请完全退出 MarketPrism 后重新打开。");
        setReportHistory([]);
        return;
      }
      setError(err instanceof Error ? `历史报告加载失败: ${err.message}` : "历史报告加载失败。");
    }
  }

  async function openHistoryReport(item: ReportSummary) {
    setError("");
    try {
      const payload = await getSavedReport(item.id);
      setReport(payload.report);
      setReportTitle(payload.title);
      setActiveReportId(item.id);
    } catch (err) {
      setError(err instanceof Error ? `历史报告读取失败: ${err.message}` : "历史报告读取失败。");
    }
  }

  function exportCurrentReport() {
    if (!report) return;
    if (!printReportAsPdf(report, reportTitle)) {
      setError("无法打开 PDF 导出面板，请检查系统打印服务是否可用。");
    }
  }

  async function runAnalysis() {
    if (!selectedStock) return;
    setBusy(true);
    setError("");
    setReport("");
    setReportTitle("中文报告");
    setActiveReportId(null);
    setEvents([{ type: "status", message: "正在保存配置。" }]);
    try {
      const nextConfig = {
        output_language: "Simplified Chinese",
        primary_market: selectedMarket,
        max_debate_rounds: depth,
        max_risk_discuss_rounds: depth,
      };
      await updateConfig(nextConfig);
      const task = await startAnalysis({
        ticker: selectedStock.symbol,
        analysis_date: analysisDate,
        analysts: selectedAnalysts,
        research_depth: depth,
      });
      let finished = false;
      const source = subscribeAnalysis(
        task.analysis_id,
        async (event) => {
          setEvents((current) => [...current, event]);
          if (event.type === "completed") {
            finished = true;
            source.close();
            const payload = await getReport(task.analysis_id);
            setReport(payload.report || "");
            setReportTitle(`${selectedStock.name} ${selectedStock.symbol} 分析报告`);
            setActiveReportId(payload.report_id || event.report_id || null);
            void loadReportHistory();
            setBusy(false);
          }
          if (event.type === "error") {
            finished = true;
            source.close();
            setBusy(false);
          }
        },
        async (err) => {
          if (finished) return;
          source.close();
          setEvents((current) => [...current, { type: "status", message: "进度连接中断，正在确认服务器结果。" }]);
          try {
            const payload = await getCompletedReportIfReady(task.analysis_id);
            if (payload) {
              finished = true;
              setEvents((current) => [...current, { type: "completed", message: "分析完成。", report_id: payload.report_id || undefined }]);
              setReport(payload.report || "");
              setReportTitle(`${selectedStock.name} ${selectedStock.symbol} 分析报告`);
              setActiveReportId(payload.report_id || null);
              void loadReportHistory();
              return;
            }
            setError(err.message);
          } catch (reportErr) {
            setError(reportErr instanceof Error ? reportErr.message : err.message);
          } finally {
            setBusy(false);
          }
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动分析失败。");
      setBusy(false);
    }
  }

  if (!session?.authenticated) {
    return (
      <main className="login-shell">
        <section className="login-panel">
          <div className="brand-row">
            <div className="brand-mark"><BarChart3 size={22} /></div>
            <div>
              <p className="eyebrow">MarketPrism</p>
              <h1>登录智能分析台</h1>
            </div>
          </div>

          <form className="auth-form" onSubmit={handleLogin}>
            <label>
              <span className="field-label">账号</span>
              <div className="secret-field auth-field">
                <UserRound size={17} />
                <input value={loginName} onChange={(event) => setLoginName(event.target.value)} placeholder="请输入账号" required />
              </div>
            </label>
            <label>
              <span className="field-label">密码</span>
              <div className="secret-field auth-field">
                <KeyRound size={17} />
                <input value={loginPassword} type="password" onChange={(event) => setLoginPassword(event.target.value)} placeholder="请输入密码" required />
              </div>
            </label>

            {(loginError || error) && <div className="error-box login-error">{loginError || error}</div>}

            <button
              className="login-button"
              disabled={booting || loginBusy || !loginName.trim() || !loginPassword}
              type="submit"
            >
              {booting || loginBusy ? <Loader2 className="spin" size={18} /> : <LogIn size={18} />}
              {booting ? "正在启动，请稍候" : loginBusy ? "登录中" : "登录"}
            </button>
            <p className="auth-note">暂不开放注册，请使用管理员分配的账号登录。</p>
          </form>
        </section>

        <section className="login-visual" aria-hidden="true">
          <div className="login-visual-head">
            <ShieldCheck size={22} />
            <span>受控访问</span>
          </div>
          <div className="login-visual-chart">
            <svg viewBox="0 0 720 240">
              <path d="M18 190 C96 174 122 112 192 126 C263 140 292 64 362 82 C438 102 448 174 524 144 C592 118 626 72 704 92" />
              <path className="muted-line" d="M18 212 C98 194 148 182 214 188 C304 198 350 152 430 164 C514 178 574 134 704 146" />
            </svg>
          </div>
          <div className="login-visual-grid">
            <span>A 股</span>
            <span>港股</span>
            <span>美股</span>
            <span>中文报告</span>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="workspace">
        <aside className="control-pane">
          <div className="brand-row">
            <div className="brand-mark"><BarChart3 size={22} /></div>
            <div>
              <p className="eyebrow">MarketPrism</p>
              <h1>多市场智能分析台</h1>
            </div>
          </div>

          <div className="step-block">
            <div className="step-title"><Search size={18} />选择股票</div>
            <div className="market-tabs" role="tablist" aria-label="选择市场">
              {marketOptions.map((market) => (
                <button
                  className={selectedMarket === market.key ? "active" : ""}
                  key={market.key}
                  onClick={() => switchMarket(market.key)}
                  type="button"
                >
                  <strong>{market.label}</strong>
                  {market.hint && <small>{market.hint}</small>}
                </button>
              ))}
            </div>
            <label className="field-label">股票代码或名称</label>
            <input
              className="text-input"
              placeholder={marketOptions.find((item) => item.key === selectedMarket)?.placeholder}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="stock-list">
              {stockResults.map((stock) => (
                <button
                  className={`stock-row ${selectedStock?.symbol === stock.symbol ? "selected" : ""}`}
                  key={stock.symbol}
                  onClick={() => setSelectedStock(stock)}
                >
                  <span>
                    <strong>{stock.name}</strong>
                    <small>{stock.market}</small>
                  </span>
                  <code>{stock.symbol}</code>
                </button>
              ))}
            </div>
          </div>

          <div className="step-block">
            <div className="step-title"><CalendarDays size={18} />日期</div>
            <input className="text-input" type="date" value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} />
          </div>

          <div className="step-block">
            <div className="step-title"><Settings2 size={18} />分析设置</div>
            <input className="range" type="range" min={1} max={5} value={depth} onChange={(event) => setDepth(Number(event.target.value))} />
            <div className="range-labels"><span>快速</span><strong>{depth}</strong><span>深入</span></div>
            <p className="depth-help">数字表示研究讨论和风险讨论轮数，当前为 {depth} 轮；数值越大，分析更细，但耗时和模型消耗也会增加。</p>
            <div className="analyst-grid">
              {analystOptions.map((analyst) => (
                <button
                  className={selectedAnalysts.includes(analyst.key) ? "active" : ""}
                  key={analyst.key}
                  onClick={() => toggleAnalyst(analyst.key)}
                >
                  {analyst.label}
                </button>
              ))}
            </div>
          </div>

          <button className="run-button" disabled={!canRun || busy} onClick={() => void runAnalysis()}>
            {busy ? <Loader2 className="spin" size={18} /> : <Play size={18} />}
            开始分析
          </button>
          {busy && <AnalysisRunHint depth={depth} events={events} selectedAnalysts={selectedAnalysts} />}

          <div className="history-panel">
            <div className="section-head history-head">
              <h3><History size={17} />历史分析报告</h3>
              <button className="mini-action" type="button" onClick={() => void loadReportHistory()}>
                <RefreshCw size={14} />
                刷新
              </button>
            </div>
            {reportHistory.length === 0 ? (
              <p className="empty history-empty">暂无历史报告，完成一次分析后会自动保存。</p>
            ) : (
              <div className="history-list">
                {reportHistory.map((item) => (
                  <button
                    className={`history-row ${activeReportId === item.id ? "active" : ""}`}
                    key={item.id}
                    onClick={() => void openHistoryReport(item)}
                    type="button"
                  >
                    <span>{item.title}</span>
                    <small>{item.analysis_date || item.created_at}</small>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        <section className="report-pane">
          <div className="status-strip">
            <div>
              <p className="eyebrow">当前任务</p>
              <h2>{selectedStock ? `${selectedStock.name} ${selectedStock.symbol}` : "等待选择股票"}</h2>
            </div>
            <div className="session-actions">
              <div className="status-pill">
                <CheckCircle2 size={17} />
                {config ? "本地配置已载入" : "正在启动，请稍候"}
              </div>
              <div className="status-pill user-pill">
                <ShieldCheck size={17} />
                {sessionUserLabel(session)}
              </div>
              <div className={`status-pill license-pill ${session?.expires_at && session.expires_in_seconds === 0 ? "expired" : ""}`}>
                <CalendarDays size={17} />
                <span>
                  <small>授权有效期</small>
                  {licenseRemainingLabel(session)}
                </span>
              </div>
              <button className="logout-button" type="button" onClick={() => void handleLogout()}>
                <LogOut size={16} />
                退出
              </button>
            </div>
          </div>

          <AnalysisWarRoom
            busy={busy}
            depth={depth}
            events={events}
            selectedAnalysts={selectedAnalysts}
            stockLabel={selectedStock ? `${selectedStock.name} ${selectedStock.symbol}` : undefined}
          />

          {error && <div className="error-box">{error}</div>}

          <div className="event-log">
            <div className="section-head">
              <h3>运行进度</h3>
              <span>{events.length} 条事件</span>
            </div>
            {events.length === 0 ? (
              <p className="empty">配置完成后点击开始分析，系统会在这里显示进度。</p>
            ) : (
              events.map((event, index) => (
                <div className={`event-row ${event.type}`} key={`${event.type}-${index}`}>
                  <span>{eventTypeLabel(event.type)}</span>
                  <p>{event.message}</p>
                </div>
              ))
            )}
          </div>

          <div className="report-view">
            <div className="section-head">
              <h3><FileText size={18} />{reportTitle}</h3>
              <div className="report-actions">
                <span>{report ? "已生成" : "尚未生成"}</span>
                <button className="mini-action" disabled={!report} type="button" onClick={exportCurrentReport}>
                  <Download size={14} />
                  导出 PDF
                </button>
              </div>
            </div>
            <pre>{report || "报告会在分析完成后显示。首版只提供研究分析，不连接券商，也不会自动下单。"}</pre>
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;
