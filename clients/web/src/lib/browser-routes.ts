interface BrowserLocation { protocol: string; host: string; }

export function langGraphApiUrl(location: BrowserLocation, endpoint: string): string {
  const url = new URL(endpoint, `${location.protocol}//${location.host}`);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("LangGraph requires HTTP or HTTPS");
  return url.toString().replace(/\/$/, "");
}

export function terminalWebSocketUrl(location: BrowserLocation, endpoint: string): URL {
  const url = new URL(endpoint, `${location.protocol}//${location.host}`);
  if (url.protocol === "https:") url.protocol = "wss:";
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol !== "ws:" && url.protocol !== "wss:") throw new Error("Terminal requires WS or WSS");
  return url;
}
