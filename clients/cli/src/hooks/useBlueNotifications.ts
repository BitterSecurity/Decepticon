import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { readBlueCursor, writeBlueCursor } from "../utils/blueCursor.js";

const notificationSchema = z.object({
  seq: z.number().int().nonnegative(),
  kind: z.enum(["detected", "assessment", "analysis_error", "watch_alert", "watch_error", "coverage_gap", "coverage_restored"]),
  payload: z.object({
    id: z.string(), rule_id: z.string().optional(), severity: z.string().optional(),
    analysis: z.string().optional(), start_seq: z.number().optional(), end_seq: z.number().optional(),
    error: z.string().optional(), reason: z.string().optional(), revision: z.union([z.string(), z.number()]).optional(),
    counters: z.record(z.string(), z.number()).optional(),
    evidence: z.object({ method: z.string().optional(), uri: z.string().optional(), status: z.number().optional(), message_excerpt: z.string().optional(), event_seqs: z.array(z.number()).optional() }).optional(),
  }).passthrough(),
});
const pageSchema = z.object({ notifications: z.array(notificationSchema), next_after: z.number().optional(), latest_seq: z.number().optional(), has_more: z.boolean() });
export type BlueNotification = z.infer<typeof notificationSchema>;
export interface BlueState {
  readonly status: "connected" | "disconnected" | "unavailable";
  readonly notifications: readonly BlueNotification[];
  readonly unreadCount: number;
  readonly lastSequence: number;
  readonly collectorStatus: "unknown" | "healthy" | "gap";
  readonly aiStatus: "unknown" | "healthy" | "error";
  readonly error: string | null;
}
export function formatBlueNotification(notification: BlueNotification, language = process.env.DECEPTICON_LANGUAGE || "en"): string {
  const { payload } = notification;
  if (language.toLowerCase().startsWith("ko")) {
    switch (notification.kind) {
      case "coverage_gap": {
        const reason = {
          collector_restarted: "수집기가 재시작되었습니다. 수집 카운터와 최근 이벤트의 연속성을 확인하세요.",
          receiver_state_replaced: "수신 저장소가 교체되거나 초기화되었습니다. 이전 관측 기록의 연속성을 확인하세요.",
          collector_data_loss: "수집 과정에서 기록이 유실되었습니다. /blue metrics에서 확인하세요. 이 구간에 공격이 없었다고 판단할 수 없습니다.",
        }[payload.reason ?? ""] ?? "수집기 또는 수신기가 응답하지 않습니다. /blue status에서 확인하세요.";
        return `[Blue Cell] 관측 공백: ${reason}`;
      }
      case "coverage_restored": return "[Blue Cell] 수집기가 다시 응답합니다. 이전 관측 공백은 여전히 확인이 필요합니다.";
      case "watch_alert": return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} 상시 감시 경보. 근거 이벤트 ${payload.evidence?.event_seqs?.join(", ") ?? "?"}, 사건 ${payload.id}.`;
      case "watch_error": return `[Blue Cell] 이벤트 ${payload.start_seq ?? "?"}–${payload.end_seq ?? "?"} AI 감시 실패. ${payload.error ?? "감시 상태를 확인하세요."}`;
      case "assessment": return `[Blue Cell 조사] 사건 ${payload.id}: 평가가 준비되었습니다. /alerts에서 확인하세요.`;
      case "analysis_error": return `[Blue Cell] 사건 ${payload.id}의 자동 조사가 완료되지 않았습니다. /blue analyze로 다시 시도하세요.`;
      default: return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} ${payload.rule_id ?? "탐지"} 감지. ` +
        `${payload.evidence?.message_excerpt?.slice(0, 100) ?? [payload.evidence?.method, payload.evidence?.uri].filter(Boolean).join(" ")} ` +
        `사건 ${payload.id}. 실제 영향은 조사 중입니다.`;
    }
  }
  if (notification.kind === "coverage_gap") {
    if (payload.reason === "collector_restarted") {
      return "[Blue Cell] Collector restarted. Review the persisted collection counters and recent target events for observation continuity.";
    }
    if (payload.reason === "receiver_state_replaced") {
      return "[Blue Cell] Receiver store was replaced or reset. Verify the continuity of prior observations.";
    }
    if (payload.reason === "collector_data_loss") {
      const counters = Object.entries(payload.counters ?? {})
        .map(([name, count]) => `${name}=${count}`).join(", ");
      return `[Blue Cell] Coverage gap: the collection pipeline dropped records. ${counters || "See /blue metrics for details."} The absence of attacks in this window cannot be established.`;
    }
    return "[Blue Cell] Coverage gap: the target log collector or receiver is not responding. Check /blue status.";
  }
  if (notification.kind === "coverage_restored") {
    return "[Blue Cell] Collector responded again. Previous coverage gaps cannot be assumed absent.";
  }
  if (notification.kind === "watch_alert") {
    const sequences = payload.evidence?.event_seqs?.join(", ") ?? "?";
    return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} standing-watch alert. ` +
      `` +
      `Evidence events ${sequences}, incident ${payload.id}.`;
  }
  if (notification.kind === "watch_error") {
    return `[Blue Cell] events ${payload.start_seq ?? "?"}–${payload.end_seq ?? "?"} –` +
      `AI watch failed. ${payload.error ?? "Check the watch state."}`;
  }
  if (notification.kind === "assessment") {
    return `[Blue Cell investigation] incident ${payload.id}: assessment ready. /alerts for details.`;
  }
  if (notification.kind === "analysis_error") {
    return `[Blue Cell] incident ${payload.id} automatic investigation did not complete. Retry with /blue analyze.`;
  }
  const evidence = payload.evidence ?? {};
  if (evidence.message_excerpt) {
    return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} ${payload.rule_id ?? "detection"} detection. ` +
      `Target log: ${evidence.message_excerpt.slice(0, 100)} incident ${payload.id}. Real impact under investigation.`;
  }
  const request = [evidence.method, evidence.uri].filter(Boolean).join(" ");
  return `[Blue Cell] ${(payload.severity ?? "unknown").toUpperCase()} ${payload.rule_id ?? "detection"} detection. ` +
    `Request ${request || "unknown"}, HTTP ${evidence.status ?? "?"}, ` +
    `incident ${payload.id}. Real impact under investigation.`;
}

export function useBlueNotifications(addSystemEvent: (message: string) => void) {
  const callback = useRef(addSystemEvent);
  callback.current = addSystemEvent;
  const url = (process.env.BLUE_MONITOR_URL ?? "http://127.0.0.1:18085").replace(/\/$/, "");
  const [state, setState] = useState<BlueState>({ status: "unavailable", notifications: [], unreadCount: 0, lastSequence: 0, collectorStatus: "unknown", aiStatus: "unknown", error: null });
  const cursor = useRef({ after: 0, acknowledged: 0 });
  const persist = useCallback(() => {
    try { writeBlueCursor(url, cursor.current); }
    catch (error) { setState(previous => ({ ...previous, error: `Blue cursor save failed: ${error instanceof Error ? error.message : String(error)}` })); }
  }, [url]);
  const acknowledge = useCallback(() => {
    cursor.current.acknowledged = cursor.current.after;
    setState(previous => ({ ...previous, unreadCount: 0 }));
    persist();
  }, [persist]);
  useEffect(() => {
    try { cursor.current = readBlueCursor(url); }
    catch (error) { setState(previous => ({ ...previous, error: `Blue cursor load failed: ${error instanceof Error ? error.message : String(error)}` })); }
    setState(previous => ({ ...previous, lastSequence: cursor.current.after }));
    let stopped = false;
    let polling = false;
    let connected = false;
    let disconnected = false;
    let failureStreak = 0;
    let nextPollAt = 0;
    const revisions = new Set<string>();
    async function poll(): Promise<void> {
      if (stopped || polling || Date.now() < nextPollAt) return;
      polling = true;
      try {
        for (let pageNumber = 0; pageNumber < 10 && !stopped; pageNumber++) {
          // Existing monitor fetch policy: bounded timeout and retry on the next poll.
          const response = await fetch(`${url}/notifications?after=${cursor.current.after}&limit=100`, { signal: AbortSignal.timeout(3000) });
          if (!response.ok) throw new DOMException(`Blue monitor HTTP ${response.status}`, "NetworkError");
          const page = pageSchema.parse(await response.json());
          if (stopped) return;
          connected = true;
          disconnected = false;
          failureStreak = 0;
          nextPollAt = 0;
          if ((page.latest_seq ?? page.next_after) !== undefined && (page.latest_seq ?? page.next_after ?? cursor.current.after) < cursor.current.after) {
            cursor.current = { after: 0, acknowledged: 0 };
            revisions.clear();
            setState(previous => ({ ...previous, notifications: [], unreadCount: 0, collectorStatus: "gap", lastSequence: 0 }));
            callback.current("[Blue Cell] Receiver sequence reset; observation continuity needs review.");
            persist();
            continue;
          }
          setState(previous => ({ ...previous, status: "connected", error: null }));
          for (const notification of page.notifications) {
            if (notification.seq <= cursor.current.after) continue;
            cursor.current.after = notification.seq;
            const revision = JSON.stringify([notification.payload.id, notification.kind, notification.payload.revision ?? notification.payload]);
            if (revisions.has(revision)) continue;
            revisions.add(revision);
            callback.current(formatBlueNotification(notification));
            setState(previous => ({ ...previous,
              notifications: [...previous.notifications, notification].slice(-500),
              lastSequence: notification.seq,
              unreadCount: previous.unreadCount + Number(notification.seq > cursor.current.acknowledged),
              collectorStatus: notification.kind === "coverage_gap" ? "gap" : notification.kind === "coverage_restored" ? "healthy" : previous.collectorStatus,
              aiStatus: notification.kind === "watch_error" || notification.kind === "analysis_error" ? "error" : notification.kind === "assessment" || notification.kind === "watch_alert" ? "healthy" : previous.aiStatus,
            }));
          }
          setState(previous => ({ ...previous, lastSequence: cursor.current.after }));
          persist();
          if (!page.has_more) break;
        }
      } catch (error) {
        if (stopped) return;
        const message = error instanceof Error ? error.message : String(error);
        setState(previous => ({ ...previous, status: connected ? "disconnected" : "unavailable", error: message, collectorStatus: "unknown", aiStatus: "unknown" }));
        if (connected && !disconnected) callback.current(`[Blue Cell] Monitor disconnected: ${message}`);
        disconnected = true;
        // The Blue stack is optional. Bound retries when it is unavailable;
        // keep the existing overlap guard and restore 1 s polling on success.
        failureStreak = Math.min(failureStreak + 1, 7);
        if (failureStreak >= 2) {
          nextPollAt = Date.now() + Math.min(1000 * 2 ** (failureStreak - 2), 30000);
        }
      } finally { polling = false; }
    }
    void poll();
    const timer = setInterval(() => { void poll(); }, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [url, persist]);
  return { ...state, acknowledge, getDetails: (id: string) => state.notifications.filter(notification => notification.payload.id === id) };
}

export function formatBlueNotificationDetails(notification: BlueNotification): string {
  const { analysis, ...evidence } = notification.payload;
  return [formatBlueNotification(notification), analysis, JSON.stringify(evidence, null, 2)].filter(Boolean).join("\n\n");
}
