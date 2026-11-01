import { describe, expect, it } from "vitest";
import { waitForBackendConfig } from "./backendReady";
import type { DesktopConfig } from "./api";

function config(): DesktopConfig {
  return {
    llm_provider: "custom_openai_compatible",
    backend_url: "https://gateway.example.com/v1",
    quick_think_llm: "qwen-fast",
    deep_think_llm: "qwen-pro",
    output_language: "Simplified Chinese",
    primary_market: "cn_a_share",
    tushare_token: null,
    max_debate_rounds: 1,
    max_risk_discuss_rounds: 1,
    data_vendors: {
      cn_stock: "akshare",
      core_stock_apis: "china_stock",
      technical_indicators: "china_stock",
      fundamental_data: "china_stock",
      news_data: "china_stock",
    },
  };
}

describe("waitForBackendConfig", () => {
  it("retries config loading until the local API is ready", async () => {
    let attempts = 0;

    const result = await waitForBackendConfig(
      async () => {
        attempts += 1;
        if (attempts < 3) {
          throw new Error("not ready");
        }
        return config();
      },
      { attempts: 3, retryDelayMs: 0 },
    );

    expect(result.quick_think_llm).toBe("qwen-fast");
    expect(attempts).toBe(3);
  });

  it("throws the last error when the local API never becomes ready", async () => {
    await expect(
      waitForBackendConfig(
        async () => {
          throw new Error("still down");
        },
        { attempts: 2, retryDelayMs: 0 },
      ),
    ).rejects.toThrow("still down");
  });
});
