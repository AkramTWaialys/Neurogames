/**
 * HistoryManager — localStorage-backed session history (FIFO, 50 max)
 * Ported from web_frontend/js/core.js
 *
 * IMPORTANT: The history key is scoped per player ID to prevent sessions
 * from leaking across different players on the same browser.
 */

export interface HistoryEntry {
  id: string;
  game: string;
  gameName: string;
  icon: string;
  timestamp: number;
  accuracy: number;           // 0-1 scale internally
  stars: string;
  level: number;
  duration_s: number;
  displayMetrics: { label: string; value: string | number }[];
  correct?: number;
  totalActions?: number;
  reactionTime?: number;      // seconds
  performanceLevel?: string;
}

const LEGACY_KEY = "neuro_history";  // old global key — migrated on first read
const HISTORY_LIMIT = 50;

/**
 * Return the per-player storage key.
 * Falls back to the legacy global key only when no player is logged in.
 */
function historyKey(): string {
  if (typeof window === "undefined") return LEGACY_KEY;
  try {
    const p = JSON.parse(localStorage.getItem("neuro_player") || "null");
    if (p?.id) return `neuro_history_${p.id}`;
  } catch { /* ignore */ }
  return LEGACY_KEY;
}

/**
 * One-time migration: if the legacy global key exists and the per-player key
 * is empty, move the entries over so existing data isn't lost on first login.
 */
function migrateLegacyKey(key: string) {
  if (key === LEGACY_KEY) return; // no player logged in, nothing to migrate
  if (localStorage.getItem(key)) return; // player key already exists
  const legacy = localStorage.getItem(LEGACY_KEY);
  if (legacy) {
    // Move old data to player-scoped key, then remove the global one
    localStorage.setItem(key, legacy);
    localStorage.removeItem(LEGACY_KEY);
  }
}

/**
 * Always read fresh from localStorage to avoid stale data
 * across page navigations in Next.js.
 */
function readStorage(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const key = historyKey();
    migrateLegacyKey(key);
    return JSON.parse(localStorage.getItem(key) || "[]") as HistoryEntry[];
  } catch {
    return [];
  }
}

function writeStorage(entries: HistoryEntry[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(historyKey(), JSON.stringify(entries));
  } catch (e) {
    console.warn("[History] localStorage write failed", e);
  }
}

export const HistoryManager = {
  /** Returns a fresh copy from localStorage every time (React-safe). */
  getAll(): HistoryEntry[] {
    return [...readStorage()];
  },

  getByGame(gameKey: string): HistoryEntry[] {
    return readStorage().filter((e) => e.game === gameKey);
  },

  push(entry: HistoryEntry) {
    console.log("[HistoryManager] pushing entry:", entry);
    const entries = readStorage();
    entries.unshift(entry); // newest first
    const trimmed = entries.length > HISTORY_LIMIT
      ? entries.slice(0, HISTORY_LIMIT)
      : entries;
    writeStorage(trimmed);
  },

  /** Last N accuracies for a game (oldest→newest) for spark charts */
  sparkData(gameKey: string, n = 5): number[] {
    return readStorage()
      .filter((e) => e.game === gameKey)
      .slice(0, n)
      .reverse()
      .map((e) => e.accuracy);
  },

  /** Previous accuracy for a game (for trend comparison) */
  previousAccuracy(gameKey: string, currentId: string): number | null {
    const items = readStorage().filter((e) => e.game === gameKey && e.id !== currentId);
    return items.length > 0 ? items[0].accuracy : null;
  },

  /**
   * Clear history for the current player only.
   * Useful for testers who want to reset their local data.
   * Does NOT affect other players' data.
   */
  clearHistory() {
    if (typeof window === "undefined") return;
    localStorage.removeItem(historyKey());
  },
};
