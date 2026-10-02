import { useEffect } from "react";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

interface BlueNotification {
  seq: number;
  kind: "detected" | "assessment" | "analysis_error";
  payload: {
    id: string;
    rule_id?: string;
    severity?: string;
    analysis?: string;
    evidence?: {
      method?: string;
      uri?: string;
      status?: number;
      message_excerpt?: string;
    };
  };
}

interface NotificationPage {
  notifications: BlueNotification[];
  next_after: number;
  has_more: boolean;
}

export function formatBlueNotification(notification: BlueNotification): string {
  const { payload } = notification;
  if (notification.kind === "assessment") {
    return `[Blue Cell 조사] 사건 ${payload.id}\n${(payload.analysis ?? "조사 결과가 없습니다.").slice(0, 4000)}`;
  }
  if (notification.kind === "analysis_error") {
    return `[Blue Cell] 사건 ${payload.id} 자동 조사를 완료하지 못했습니다. /blue analyze로 다시 조사할 수 있습니다.`;
  }
  const evidence = payload.evidence ?? {};
  if (evidence.message_excerpt) {
    return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} ${payload.rule_id ?? "detection"} 탐지. ` +
      `대상 로그: ${evidence.message_excerpt.slice(0, 500)} 사건 ${payload.id}. 실제 영향은 조사 중입니다.`;
  }
  const request = [evidence.method, evidence.uri].filter(Boolean).join(" ");
  return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} ${payload.rule_id ?? "detection"} 탐지. ` +
    `요청 ${request || "알 수 없음"}, HTTP ${evidence.status ?? "?"}, ` +
    `사건 ${payload.id}. 실제 영향은 조사 중입니다.`;
}

export function useBlueNotifications(addSystemEvent: (message: string) => void): void {
  useEffect(() => {
    const url = (process.env.BLUE_MONITOR_URL ?? "http://127.0.0.1:18085").replace(/\/$/, "");
    const statePath = process.env.DECEPTICON_HOME
      ? join(process.env.DECEPTICON_HOME, "blue-notifications.json")
      : null;
    let after = 0;
    let stopped = false;
    let polling = false;

    async function poll(): Promise<void> {
      if (stopped || polling) return;
      polling = true;
      try {
        for (let pageNumber = 0; pageNumber < 10 && !stopped; pageNumber++) {
          const response = await fetch(`${url}/notifications?after=${after}&limit=100`, {
            signal: AbortSignal.timeout(3000),
          });
          if (!response.ok) return;
          const page = (await response.json()) as NotificationPage;
          if (!Array.isArray(page.notifications) || stopped) return;
          for (const notification of page.notifications) {
            addSystemEvent(formatBlueNotification(notification));
            after = notification.seq;
          }
          if (statePath && page.notifications.length) {
            await writeFile(statePath, JSON.stringify({ after }), "utf8");
          }
          if (!page.has_more) break;
        }
      } catch {
        return;
      } finally {
        polling = false;
      }
    }

    async function start(): Promise<void> {
      if (statePath) {
        try {
          const saved = JSON.parse(await readFile(statePath, "utf8")) as { after?: unknown };
          if (typeof saved.after === "number" && Number.isSafeInteger(saved.after) && saved.after >= 0) {
            after = saved.after;
          }
        } catch {
          after = 0;
        }
      }
      await poll();
    }

    void start();
    const timer = setInterval(() => { void poll(); }, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [addSystemEvent]);
}
