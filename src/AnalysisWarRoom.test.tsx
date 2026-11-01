import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnalysisRunHint, AnalysisWarRoom } from "./AnalysisWarRoom";

describe("AnalysisWarRoom", () => {
  it("shows only the selected analyst teams in standby", () => {
    const markup = renderToStaticMarkup(
      <AnalysisWarRoom
        busy={false}
        depth={3}
        events={[]}
        selectedAnalysts={["market", "news"]}
        stockLabel="贵州茅台 600519.SH"
      />,
    );

    expect(markup).toContain("AI 投研作战室");
    expect(markup).toContain("待命");
    expect(markup).toContain("技术分析");
    expect(markup).toContain("新闻");
    expect(markup).not.toContain("基本面");
    expect(markup).not.toContain("情绪");
  });

  it("shows a running discussion with team count and planned debate depth", () => {
    const markup = renderToStaticMarkup(
      <AnalysisWarRoom
        busy
        depth={2}
        events={[{ type: "status", message: "正在获取行情数据。" }]}
        selectedAnalysts={["market", "fundamentals", "social"]}
        stockLabel="贵州茅台 600519.SH"
      />,
    );

    expect(markup).toContain("正在组织 3 个团队，计划共 2 轮讨论");
    expect(markup).not.toContain("第 2 轮讨论");
    expect(markup).toContain("正在获取行情数据。");
    expect(markup).toContain("war-room--running");
  });

  it("switches to completed and failed state from events", () => {
    const completed = renderToStaticMarkup(
      <AnalysisWarRoom
        busy={false}
        depth={1}
        events={[{ type: "completed", message: "分析完成。" }]}
        selectedAnalysts={["market"]}
      />,
    );
    const failed = renderToStaticMarkup(
      <AnalysisWarRoom
        busy={false}
        depth={1}
        events={[{ type: "error", message: "分析中断。" }]}
        selectedAnalysts={["market"]}
      />,
    );

    expect(completed).toContain("报告已生成，观点已汇总");
    expect(failed).toContain("分析中断，请查看错误信息");
  });
});

describe("AnalysisRunHint", () => {
  it("renders the compact running hint with the latest event", () => {
    const markup = renderToStaticMarkup(
      <AnalysisRunHint
        depth={4}
        events={[{ type: "status", message: "正在生成中文报告。" }]}
        selectedAnalysts={["market", "fundamentals", "news", "social"]}
      />,
    );

    expect(markup).toContain("正在组织 4 个团队，计划共 4 轮讨论");
    expect(markup).not.toContain("第 4 轮讨论");
    expect(markup).toContain("正在生成中文报告。");
  });
});
