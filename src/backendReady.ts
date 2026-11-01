import type { DesktopConfig } from "./api";

type WaitOptions = {
  attempts?: number;
  retryDelayMs?: number;
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

export async function waitForBackendConfig(
  loadConfig: () => Promise<DesktopConfig>,
  options: WaitOptions = {},
): Promise<DesktopConfig> {
  const attempts = options.attempts ?? 30;
  const retryDelayMs = options.retryDelayMs ?? 300;
  let lastError: unknown;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await loadConfig();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await delay(retryDelayMs);
      }
    }
  }

  throw lastError instanceof Error ? lastError : new Error("本地 API 未就绪。");
}
