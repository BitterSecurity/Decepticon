export interface BrowserConfig {
  langGraphApiUrl: string;
  terminalWsUrl: string;
}

// Pass process.env from the request-time server component: direct
// process.env.NEXT_PUBLIC_* references would be frozen by next build.
export function browserConfig(env: Record<string, string | undefined>): BrowserConfig {
  return {
    langGraphApiUrl: env.NEXT_PUBLIC_LANGGRAPH_API_URL ?? "http://localhost:2024",
    terminalWsUrl: env.NEXT_PUBLIC_TERMINAL_WS_URL ?? "ws://localhost:3003",
  };
}

interface BrowserLocation {
  protocol: string;
  host: string;
}

export function langGraphApiUrl(location: BrowserLocation, endpoint: string): string {
  const url = new URL(endpoint, `${location.protocol}//${location.host}`);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("LangGraph requires HTTP or HTTPS");
  }
  return url.toString().replace(/\/$/, "");
}

export function terminalWebSocketUrl(location: BrowserLocation, endpoint: string): URL {
  const url = new URL(endpoint, `${location.protocol}//${location.host}`);
  if (url.protocol === "https:") url.protocol = "wss:";
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new TypeError("Terminal requires WS or WSS");
  }
  return url;
}
