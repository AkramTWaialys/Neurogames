/**
 * GamificationManager — streak, XP, badges, consistency
 * Ported from web_frontend/js/core.js
 */

import { HistoryManager, type HistoryEntry } from "./history";

function dateKey(ts: number): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export const GamificationManager = {
  /** Current consecutive-day streak (including today if played). */
  getStreak(): number {
    const entries = HistoryManager.getAll();
    if (entries.length === 0) return 0;
    const daySet = new Set(entries.map((e) => dateKey(e.timestamp)));
    const sortedDays = [...daySet].sort().reverse();
    let streak = 0;
    const today = dateKey(Date.now());
    const yesterday = dateKey(Date.now() - 86400000);
    if (sortedDays[0] !== today && sortedDays[0] !== yesterday) return 0;
    let cursor = new Date(sortedDays[0] + "T00:00:00");
    for (const day of sortedDays) {
      const expected = dateKey(cursor.getTime());
      if (day === expected) {
        streak++;
        cursor = new Date(cursor.getTime() - 86400000);
      } else {
        break;
      }
    }
    return streak;
  },

  /** Total XP = sum of (accuracy × level × 100) */
  getTotalXP(): number {
    return HistoryManager.getAll().reduce((sum, e) => {
      return sum + Math.round((e.accuracy || 0) * (e.level || 1) * 100);
    }, 0);
  },

  /** Number of sessions played today */
  getTodayCount(): number {
    const todayKey = dateKey(Date.now());
    return HistoryManager.getAll().filter((e) => dateKey(e.timestamp) === todayKey).length;
  },

  /** Earned badges */
  getBadges(): { icon: string; label: string }[] {
    const entries = HistoryManager.getAll();
    const badges: { icon: string; label: string }[] = [];
    if (entries.length >= 1) badges.push({ icon: "🎮", label: "badge.first" });
    if (entries.length >= 5) badges.push({ icon: "🏅", label: "badge.five" });
    if (entries.length >= 10) badges.push({ icon: "💎", label: "badge.ten" });
    if (entries.length >= 25) badges.push({ icon: "👑", label: "badge.twentyFive" });

    const streak = this.getStreak();
    if (streak >= 3) badges.push({ icon: "🔥", label: "badge.streak3" });
    if (streak >= 7) badges.push({ icon: "⚡", label: "badge.streak7" });

    const hasPerfect = entries.some((e) => e.accuracy >= 0.99);
    if (hasPerfect) badges.push({ icon: "💯", label: "badge.perfect" });

    const xp = this.getTotalXP();
    if (xp >= 500) badges.push({ icon: "🌟", label: "badge.xp500" });
    if (xp >= 1000) badges.push({ icon: "🏆", label: "badge.xp1000" });

    const gamesPlayed = new Set(entries.map((e) => e.game));
    if (gamesPlayed.size >= 5) badges.push({ icon: "🎯", label: "badge.allGames" });

    return badges;
  },

  /** 7-day consistency: how many of the last 7 days had sessions */
  getConsistency7d(): number {
    const entries = HistoryManager.getAll();
    const now = Date.now();
    const daySet = new Set<string>();
    for (const e of entries) {
      if (now - e.timestamp <= 7 * 86400000) daySet.add(dateKey(e.timestamp));
    }
    return daySet.size;
  },

  /** Group entries by day key (newest first) */
  groupByDay(entries: HistoryEntry[]): Map<string, HistoryEntry[]> {
    const map = new Map<string, HistoryEntry[]>();
    for (const e of entries) {
      const dk = dateKey(e.timestamp);
      if (!map.has(dk)) map.set(dk, []);
      map.get(dk)!.push(e);
    }
    return map;
  },

  /** Format day label (Aujourd'hui, Hier, or date with year if old) */
  formatDayLabel(dk: string): string {
    const todayKey = dateKey(Date.now());
    const yesterdayKey = dateKey(Date.now() - 86400000);
    if (dk === todayKey) return "Aujourd'hui";
    if (dk === yesterdayKey) return "Hier";
    const d = new Date(dk + "T00:00:00");
    const days = ["Dim", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam"];
    const months = ["jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep", "oct", "nov", "déc"];
    const currentYear = new Date().getFullYear();
    const label = `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}`;
    return d.getFullYear() !== currentYear ? `${label} ${d.getFullYear()}` : label;
  },
};
