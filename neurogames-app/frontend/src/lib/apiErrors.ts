function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatLocation(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  return loc
    .filter((part) => part !== "body")
    .map(String)
    .join(".");
}

function formatDetailItem(item: unknown): string {
  if (typeof item === "string") return item;
  if (!isRecord(item)) return "";

  const msg = item.msg ?? item.message ?? item.detail;
  const message = typeof msg === "string" ? msg : formatApiErrorMessage(msg, "");
  const location = formatLocation(item.loc);

  if (location && message) return `${location}: ${message}`;
  return message;
}

export function formatApiErrorMessage(payload: unknown, fallback = "Request failed"): string {
  if (typeof payload === "string") return payload;
  if (Array.isArray(payload)) {
    const messages = payload.map(formatDetailItem).filter(Boolean);
    return messages.length ? messages.join("; ") : fallback;
  }
  if (isRecord(payload)) {
    if ("detail" in payload) return formatApiErrorMessage(payload.detail, fallback);
    if (typeof payload.message === "string") return payload.message;
    if (typeof payload.msg === "string") return payload.msg;
    if ("errors" in payload) return formatApiErrorMessage(payload.errors, fallback);
  }
  return fallback;
}

export async function getResponseErrorMessage(resp: Response, fallback = `HTTP ${resp.status}`): Promise<string> {
  const payload = await resp.json().catch(() => null);
  return formatApiErrorMessage(payload, fallback);
}
