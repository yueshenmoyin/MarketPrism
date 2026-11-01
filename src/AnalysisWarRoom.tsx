import type { AnalysisEvent } from "./api";

export type AnalystKey = "market" | "fundamentals" | "news" | "social";

export const analystOptions: { key: AnalystKey; label: string }[] = [
  { key: "market", label: "技术分析" },
  { key: "fundamentals", label: "基本面" },
  { key: "news", label: "新闻" },
  { key: "social", label: "情绪" },
];

type WarRoomStatus = "standby" | "running" | "completed" | "failed";

type AnalystMeta = {
  key: AnalystKey;
  label: string;
  shortLabel: string;
  deskLabel: string;
  runningLine: string;
};

const analystMeta: AnalystMeta[] = [
  {
    key: "market",
    label: "技术分析",
    shortLabel: "技",
    deskLabel: "趋势台",
    runningLine: "均线与量能正在发言",
  },
  {
    key: "fundamentals",
    label: "基本面",
    shortLabel: "基",
    deskLabel: "财务台",
    runningLine: "估值与利润正在核对",
  },
  {
    key: "news",
    label: "新闻",
    shortLabel: "新",
    deskLabel: "资讯台",
    runningLine: "公告与新闻正在补充",
  },
  {
    key: "social",
    label: "情绪",
    shortLabel: "情",
    deskLabel: "情绪台",
    runningLine: "市场情绪正在校验",
  },
];

function latestEventMessage(events: AnalysisEvent[]) {
  return events.length > 0 ? events[events.length - 1]?.message : "";
}

function warRoomStatus(busy: boolean, events: AnalysisEvent[]): WarRoomStatus {
  const latestType = events.length > 0 ? events[events.length - 1]?.type : undefined;
  if (latestType === "error") return "failed";
  if (latestType === "completed") return "completed";
  if (busy) return "running";
  return "standby";
}

function statusText(status: WarRoomStatus, teamCount: number, depth: number) {
  if (status === "running") return `正在组织 ${teamCount} 个团队，计划共 ${depth} 轮讨论`;
  if (status === "completed") return "报告已生成，观点已汇总";
  if (status === "failed") return "分析中断，请查看错误信息";
  return "待命";
}

type WarRoomProps = {
  busy: boolean;
  depth: number;
  events: AnalysisEvent[];
  selectedAnalysts: string[];
  stockLabel?: string;
};

export function AnalysisRunHint({ depth, events, selectedAnalysts }: Omit<WarRoomProps, "busy" | "stockLabel">) {
  const message = latestEventMessage(events) || "团队正在进入投研作战室。";

  return (
    <div className="run-hint" role="status" aria-live="polite">
      <div className="run-hint-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div>
        <strong>正在组织 {selectedAnalysts.length} 个团队，计划共 {depth} 轮讨论</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}

export function AnalysisWarRoom({ busy, depth, events, selectedAnalysts, stockLabel }: WarRoomProps) {
  const activeTeams = analystMeta.filter((team) => selectedAnalysts.includes(team.key));
  const status = warRoomStatus(busy, events);
  const latestMessage = latestEventMessage(events);
  const headline = statusText(status, activeTeams.length, depth);

  return (
    <section className={`market-visual war-room war-room--${status}`} aria-label="AI 投研作战室">
      <div className="war-room-grid" aria-hidden="true" />
      <div className="war-room-topbar">
        <span>AI 投研作战室</span>
        <span>{stockLabel || "等待选择股票"}</span>
        <span>{headline}</span>
      </div>

      <div className="manager-desk">
        <span className="manager-core">AI</span>
        <strong>组合经理决策台</strong>
        <small>{status === "standby" ? "等待团队入场" : headline}</small>
      </div>

      <div className="evidence-track" aria-hidden="true">
        <span className="evidence-track-fill" />
      </div>

      {activeTeams.map((team, index) => (
        <div className={`war-agent war-agent--${index}`} key={team.key}>
          <div className="agent-desk">
            <span>{team.deskLabel}</span>
            <i />
          </div>
          <div className="agent-avatar" aria-hidden="true">
            <span>{team.shortLabel}</span>
          </div>
          <div className="agent-name">{team.label}</div>
          <div className="agent-bubble">
            {status === "running" ? team.runningLine : status === "completed" ? "观点已汇总" : status === "failed" ? "等待重试" : "待命"}
          </div>
        </div>
      ))}

      {activeTeams.slice(1).map((team, index) => (
        <span className={`war-link war-link--${index}`} key={`link-${team.key}`} aria-hidden="true" />
      ))}

      <div className="war-room-footer">
        <span className="live-dot" aria-hidden="true" />
        <strong>{headline}</strong>
        <p>{latestMessage || "选择团队后点击开始分析，系统会在这里展示协作过程。"}</p>
      </div>
    </section>
  );
}
