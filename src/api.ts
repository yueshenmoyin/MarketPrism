export type Market = "cn_a_share" | "hk_stock" | "us_stock";
export type CnStockVendor = "akshare" | "baostock" | "tushare";

export type AuthUser = {
  name?: string;
  username?: string;
  account?: string;
  [key: string]: unknown;
};

export type AuthSession = {
  authenticated: boolean;
  auth_server_url?: string | null;
  user?: AuthUser | null;
  expires_at?: string | null;
  expires_in_seconds?: number | null;
};

export type DesktopConfig = {
  llm_provider: string;
  backend_url?: string | null;
  custom_base_url?: string | null;
  custom_api_key?: string | null;
  auth_server_url?: string | null;
  auth_token?: string | null;
  auth_user?: AuthUser | null;
  quick_think_llm: string;
  deep_think_llm: string;
  output_language: string;
  primary_market: Market;
  advanced_mode?: boolean;
  tushare_token?: string | null;
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  data_vendors: {
    cn_stock: CnStockVendor;
    core_stock_apis: string;
    technical_indicators: string;
    fundamental_data: string;
    news_data: string;
  };
};

export type StockSearchItem = {
  symbol: string;
  code: string;
  name: string;
  market: string;
};

export type AnalysisEvent = {
  type: "status" | "completed" | "error";
  message: string;
  decision?: string;
  report_id?: string;
};

export type ReportSummary = {
  id: string;
  title: string;
  ticker: string;
  analysis_date: string;
  created_at: string;
};

export type SavedReport = ReportSummary & {
  report: string;
};

export type AnalysisReportPayload = {
  status: string;
  report: string | null;
  report_id?: string | null;
};

const RATING_LABELS: Record<string, string> = {
  Buy: "买入",
  Overweight: "增持",
  Hold: "持有",
  Underweight: "减持",
  Sell: "卖出",
};

const REPORT_LABELS: Record<string, string> = {
  Rating: "评级",
  Recommendation: "投资建议",
  Action: "操作",
  "Final Rating": "最终评级",
  "Executive Summary": "执行摘要",
  "Investment Thesis": "投资逻辑",
  "Price Target": "目标价",
  "Time Horizon": "投资周期",
  Rationale: "理由",
  "Strategic Actions": "策略动作",
};

const API_BASE = "http://127.0.0.1:8765";

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function localizeReportText(reportText: string): string {
  let localized = reportText;
  Object.entries(REPORT_LABELS).forEach(([english, chinese]) => {
    localized = localized.replaceAll(`**${english}**:`, `**${chinese}**：`);
    localized = localized.replaceAll(`${english}:`, `${chinese}：`);
  });

  const ratingPattern = Object.keys(RATING_LABELS).map(escapeRegExp).join("|");
  const labelPattern = Object.values(REPORT_LABELS).map(escapeRegExp).join("|");
  localized = localized.replace(new RegExp(`((?:\\*\\*(?:${labelPattern})\\*\\*|(?:${labelPattern}))：)\\s+`, "g"), "$1");
  localized = localized.replace(new RegExp(`((?:-\\s*)?最终评级[:：]\\s*)(${ratingPattern})\\b`, "g"), (_, prefix: string, rating: string) => `${prefix}${RATING_LABELS[rating] ?? rating}`);
  return localized.replace(new RegExp(`((?:\\*\\*(?:${labelPattern})\\*\\*|(?:${labelPattern}))：\\s*)(${ratingPattern})\\b`, "g"), (_, prefix: string, rating: string) => `${prefix}${RATING_LABELS[rating] ?? rating}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const payload = JSON.parse(text) as { detail?: string };
      detail = payload.detail || text;
    } catch {
      detail = text;
    }
    throw new Error(detail || `请求失败: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getConfig(): Promise<DesktopConfig> {
  return request<DesktopConfig>("/config");
}

export function updateConfig(payload: Partial<DesktopConfig> & {
  custom_api_key?: string;
  custom_base_url?: string;
  cn_stock_vendor?: CnStockVendor;
  tushare_token?: string;
}): Promise<DesktopConfig> {
  return request<DesktopConfig>("/config", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAuthSession(): Promise<AuthSession> {
  return request<AuthSession>("/auth/session");
}

export function login(payload: {
  auth_server_url?: string;
  username: string;
  password: string;
}): Promise<AuthSession> {
  return request<AuthSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function logout(): Promise<AuthSession> {
  return request<AuthSession>("/auth/logout", {
    method: "POST",
  });
}

export function buildStockSearchPath(query: string, market: Market, vendor?: CnStockVendor): string {
  const params = new URLSearchParams({ q: query, market });
  if (market === "cn_a_share" && vendor) {
    params.set("vendor", vendor);
  }
  return `/stocks/search?${params.toString()}`;
}

export function shouldShowAdvancedDataControls(config: { primary_market: Market; advanced_mode?: boolean }): boolean {
  return config.primary_market === "cn_a_share" && config.advanced_mode === true;
}

export function shouldShowAdvancedModelControls(config: { advanced_mode?: boolean }): boolean {
  return config.advanced_mode === true;
}

export function searchStocks(query: string, market: Market = "cn_a_share", vendor?: CnStockVendor): Promise<{ items: StockSearchItem[] }> {
  return request<{ items: StockSearchItem[] }>(buildStockSearchPath(query, market, vendor));
}

export function startAnalysis(payload: {
  ticker: string;
  analysis_date?: string;
  analysts: string[];
  research_depth: number;
}): Promise<{ analysis_id: string; status: string }> {
  return request<{ analysis_id: string; status: string }>("/analysis/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function subscribeAnalysis(
  analysisId: string,
  onEvent: (event: AnalysisEvent) => void,
  onError: (error: Error) => void,
): EventSource {
  const source = new EventSource(`${API_BASE}/analysis/${analysisId}/events`);
  source.onmessage = (event) => onEvent(JSON.parse(event.data) as AnalysisEvent);
  source.onerror = () => onError(new Error("分析事件流已断开。"));
  return source;
}

export function getReport(analysisId: string): Promise<AnalysisReportPayload> {
  return request<AnalysisReportPayload>(`/analysis/${analysisId}/report`).then((payload) => ({
    ...payload,
    report: payload.report ? localizeReportText(payload.report) : payload.report,
  }));
}

export async function getCompletedReportIfReady(
  analysisId: string,
  reportLoader: (id: string) => Promise<AnalysisReportPayload> = getReport,
): Promise<AnalysisReportPayload | null> {
  const payload = await reportLoader(analysisId);
  if (payload.status === "completed" && payload.report) {
    return payload;
  }
  return null;
}

export function listReports(): Promise<{ items: ReportSummary[] }> {
  return request<{ items: ReportSummary[] }>("/reports");
}

export function getSavedReport(reportId: string): Promise<SavedReport> {
  return request<SavedReport>(`/reports/${encodeURIComponent(reportId)}`).then((payload) => ({
    ...payload,
    report: localizeReportText(payload.report),
  }));
}
