"use client";

import { useState, useEffect, useMemo } from "react";
import { HistoryManager, type HistoryEntry } from "@/lib/history";
import { GamificationManager } from "@/lib/gamification";
import { NeuroAPI } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";
import { translateGameName, translateMetricLabel, translatePerformanceLevel } from "@/i18n/labels";
import styles from "./page.module.css";

type GameFilter = {
  key: string;
  icon: string;
  labelKey?: string;
  label?: string;
};

const GAME_FILTERS: GameFilter[] = [
  { key: "all",      icon: "", labelKey: "history.all" },
  { key: "gonogo",   icon: "🎯", labelKey: "games.gonogo.name" },
  { key: "memory",   icon: "🃏", labelKey: "games.memory.name" },
  { key: "tracking", icon: "👁️", labelKey: "games.tracking.name" },
  { key: "shapes",   icon: "🔷", labelKey: "games.shapes.name" },
  { key: "puzzle",   icon: "🧩", labelKey: "games.puzzle.name" },
];

function StarRating({ stars }: { stars: string }) {
  return <span className={styles.stars}>{stars}</span>;
}

/** Performance level badge */
function PerfBadge({ level, t }: { level?: string; t: (key: string) => string }) {
  if (!level) return null;
  const cls = level === "Optimal" ? styles.perfOptimal
    : level === "Struggling" ? styles.perfStruggling
    : styles.perfDisengaged;
  return <span className={`${styles.perfBadge} ${cls}`}>{translatePerformanceLevel(level, t)}</span>;
}

export default function HistoryPage() {
  const { t, formatDate, formatDuration } = useI18n();
  const [filter, setFilter] = useState("all");
  const [expandedDays, setExpandedDays] = useState<Set<string>>(new Set());
  const [allEntries, setAllEntries] = useState<HistoryEntry[]>([]);
  const [streak, setStreak] = useState(0);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

  useEffect(() => {
    queueMicrotask(() => {
      // 1. Read local sessions immediately (fast, offline-first)
      const localEntries = HistoryManager.getAll();
      setAllEntries(localEntries);
      setStreak(GamificationManager.getStreak());

      // 2. Fetch individual sessions from backend for historical data
      let playerId = "demo";
      try {
        const stored = JSON.parse(localStorage.getItem("neuro_player") || "null");
        if (stored?.id) playerId = stored.id;
      } catch { /* ignore */ }

      NeuroAPI.getSessionHistory(playerId).then(({ ok, sessions }) => {
        setBackendOnline(ok);
        if (!ok || sessions.length === 0) return;

        // Convert backend SessionDetailDTO to HistoryEntry
        const backendEntries: HistoryEntry[] = sessions.map((s) => {
          const accNorm = s.accuracy / 100; // backend sends 0-100, normalize to 0-1

          return {
            id: `backend_${s.game_id}_${s.id}`,
            game: s.game_id,
            gameName: s.game_name || s.game_id,
            icon: s.icon,
            timestamp: new Date(s.stored_at).getTime() || Date.now(),
            accuracy: accNorm,
            stars: s.stars,
            level: s.level,
            duration_s: s.duration_s,
            correct: s.correct,
            totalActions: s.total_actions,
            reactionTime: s.reaction_time ?? undefined,
            performanceLevel: s.performance_level ?? undefined,
            displayMetrics: [
              { label: "Correct / Total", value: `${s.correct} / ${s.total_actions}` },
              { label: "Précision", value: `${Math.round(s.accuracy)}%` },
              ...(s.reaction_time != null ? [{ label: "Temps réaction", value: `${Math.round(s.reaction_time * 1000)}ms` }] : []),
              ...(s.performance_level ? [{ label: "Performance", value: s.performance_level }] : []),
            ],
          };
        });

        // Merge: backend entries overwrite local entries if same time window (within 1 hour)
        setAllEntries((prev) => {
          const backendTimes = backendEntries.map(e => e.timestamp);
        
          // Filter out local entries that have a backend equivalent within 1 hour
          const localClean = prev.filter(p => !p.id.startsWith("backend_") &&
            !backendTimes.some(bt => Math.abs(bt - p.timestamp) < 3600000)
          );

          // Combine and sort by timestamp descending
          const merged = [...localClean, ...backendEntries]
            .sort((a, b) => b.timestamp - a.timestamp);
          return merged;
        });
      });
    });
  }, []);

  const filters = useMemo<GameFilter[]>(() => {
    const known = new Set(GAME_FILTERS.map((item) => item.key));
    const dynamicFilters = new Map<string, GameFilter>();
    allEntries.forEach((entry) => {
      if (known.has(entry.game) || dynamicFilters.has(entry.game)) return;
      dynamicFilters.set(entry.game, {
        key: entry.game,
        icon: entry.icon || "ðŸŽ®",
        label: entry.gameName || entry.game,
      });
    });
    return [...GAME_FILTERS, ...dynamicFilters.values()];
  }, [allEntries]);

  const entries = filter === "all" ? allEntries : allEntries.filter(e => e.game === filter);
  const dayGroups = GamificationManager.groupByDay(entries);

  // Summary stats
  const totalSessions = allEntries.length;
  const avgAcc = totalSessions > 0
    ? Math.round(allEntries.reduce((s, e) => s + e.accuracy, 0) / totalSessions * 100)
    : 0;

  // Best game
  const byGame: Record<string, { sum: number; count: number; name: string }> = {};
  allEntries.forEach(e => {
    if (!byGame[e.game]) byGame[e.game] = { sum: 0, count: 0, name: e.game };
    byGame[e.game].sum += e.accuracy;
    byGame[e.game].count++;
  });
  let bestGame = "—";
  let bestAvg = -1;
  Object.entries(byGame).forEach(([, v]) => {
    const avg = v.sum / v.count;
    if (avg > bestAvg) { bestAvg = avg; bestGame = v.name; }
  });

  // Date range
  let dateRange = "";
  if (totalSessions > 0) {
    const timestamps = allEntries.map(e => e.timestamp);
    const oldest = Math.min(...timestamps);
    const newest = Math.max(...timestamps);
    dateRange = oldest === newest ? formatDate(newest) : `${formatDate(oldest)} → ${formatDate(newest)}`;
  }

  const formatDayLabel = (dk: string) => {
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);
    const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    const yesterdayKey = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`;
    if (dk === todayKey) return t("date.today");
    if (dk === yesterdayKey) return t("date.yesterday");
    return formatDate(`${dk}T00:00:00`, { weekday: "short", month: "short", day: "numeric", year: new Date(dk).getFullYear() !== today.getFullYear() ? "numeric" : undefined });
  };

  const toggleDay = (dk: string) => {
    setExpandedDays(prev => {
      const next = new Set(prev);
      if (next.has(dk)) next.delete(dk); else next.add(dk);
      return next;
    });
  };

  // Trend for an entry
  const getTrend = (entry: HistoryEntry) => {
    const prev = HistoryManager.previousAccuracy(entry.game, entry.id);
    if (prev === null) return null;
    const diff = Math.round((entry.accuracy - prev) * 100);
    if (diff > 0) return { cls: styles.trendUp, label: `↑ +${diff}%` };
    if (diff < 0) return { cls: styles.trendDown, label: `↓ ${diff}%` };
    return { cls: styles.trendNeutral, label: "━ 0%" };
  };

  return (
    <div className={styles.screen}>
      <div className={styles.wrapper}>
        <h1 className={styles.title}>{t("history.title")}</h1>
        <p className={styles.subtitle}>{t("history.subtitle")}</p>

        {/* Summary stats */}
        <div className={styles.summaryRow}>
          <div className={styles.summaryCard}>
            <span className={styles.summaryVal}>{totalSessions}</span>
            <span className={styles.summaryLbl}>{t("common.sessions")}</span>
          </div>
          <div className={styles.summaryCard}>
            <span className={styles.summaryVal}>{totalSessions > 0 ? `${avgAcc}%` : "—"}</span>
            <span className={styles.summaryLbl}>{t("metrics.avgAccuracy")}</span>
          </div>
          <div className={styles.summaryCard}>
            <span className={styles.summaryVal}>{totalSessions > 0 ? translateGameName(bestGame, t) : "—"}</span>
            <span className={styles.summaryLbl}>{t("history.bestGame")}</span>
          </div>
        </div>

        {/* Date range */}
        {dateRange && (
          <div className={styles.dateRange}>
            <span className={styles.dateRangeIcon}>📅</span>
            <span>{dateRange}</span>
            {backendOnline === true && <span className={styles.backendBadge}>🟢 {t("common.synced")}</span>}
            {backendOnline === false && <span className={styles.offlineBadge}>🔴 {t("common.offline")}</span>}
          </div>
        )}

        {/* Streak widget */}
        <div className={styles.streakWidget}>
          <div className={styles.streakLeft}>
            <div className={styles.streakRing}>
              <svg viewBox="0 0 64 64" className={styles.streakSvg}>
                <circle cx="32" cy="32" r="28" className={styles.streakBg} />
                <circle
                  cx="32" cy="32" r="28"
                  className={styles.streakFill}
                  style={{
                    strokeDasharray: `${2 * Math.PI * 28}`,
                    strokeDashoffset: `${2 * Math.PI * 28 * (1 - Math.min(1, streak / 7))}`,
                  }}
                />
              </svg>
              <span className={styles.streakIcon}>🔥</span>
            </div>
            <div>
              <div className={styles.streakLabel}>{t("history.streak")}</div>
              <div className={styles.streakValue}>{t("history.days", { count: streak })}</div>
            </div>
          </div>
        </div>

        {/* Filter tabs */}
        <div className={styles.tabs}>
          {filters.map(f => (
            <button
              key={f.key}
              className={`${styles.tab} ${filter === f.key ? styles.tabActive : ""}`}
              onClick={() => { setFilter(f.key); setExpandedDays(new Set()); }}
            >
              {f.icon ? `${f.icon} ${f.label || t(f.labelKey || f.key)}` : f.label || t(f.labelKey || f.key)}
            </button>
          ))}
        </div>

        {/* Timeline */}
        {entries.length === 0 ? (
          <div className={styles.empty}>
            <div className={styles.emptyIcon}>🎮</div>
            <p>{t("history.empty").split("\n").map((line, i) => <span key={line}>{i > 0 && <br />}{line}</span>)}</p>
          </div>
        ) : (
          <div className={styles.timeline}>
            {[...dayGroups.entries()].map(([dk, dayEntries]) => {
              const isExpanded = expandedDays.has(dk);
              const dayAvg = Math.round(dayEntries.reduce((s, e) => s + e.accuracy, 0) / dayEntries.length * 100);
              const avgClass = dayAvg >= 75 ? styles.accHigh : dayAvg >= 45 ? styles.accMed : styles.accLow;

              return (
                <div key={dk} className={styles.dayGroup}>
                  <button className={styles.dayHeader} onClick={() => toggleDay(dk)}>
                    <span className={styles.dayChevron}>{isExpanded ? "▾" : "▸"}</span>
                    <span className={styles.dayLabel}>{formatDayLabel(dk)}</span>
                    <span className={styles.dayCount}>{t("history.daySessions", { count: dayEntries.length, plural: dayEntries.length > 1 ? "s" : "" })}</span>
                    <span className={`${styles.dayAvg} ${avgClass}`}>{dayAvg}%</span>
                  </button>
                  {isExpanded && (
                    <div className={styles.dayCards}>
                      {dayEntries.map((entry, i) => {
                        const accPct = Math.round(entry.accuracy * 100);
                        const accClass = entry.accuracy >= 0.75 ? styles.accHigh : entry.accuracy >= 0.45 ? styles.accMed : styles.accLow;
                        const trend = getTrend(entry);

                        return (
                          <div key={entry.id} className={styles.card} style={{ animationDelay: `${i * 0.04}s` }}>
                            <div className={styles.cardHeader}>
                              <span className={styles.cardIcon}>{entry.icon}</span>
                              <div className={styles.cardInfo}>
                                <div className={styles.cardGame}>{translateGameName(entry.gameName, t)}</div>
                                <StarRating stars={entry.stars} />
                              </div>
                              <PerfBadge level={entry.performanceLevel} t={t} />
                              {trend && <span className={trend.cls}>{trend.label}</span>}
                            </div>
                            <div className={styles.accBarWrap}>
                              <div className={styles.accBar}>
                                <div className={`${styles.accFill} ${accClass}`} style={{ width: `${accPct}%` }} />
                              </div>
                              <span className={styles.accLabel}>{accPct}%</span>
                            </div>

                            {/* Enriched metrics grid */}
                            <div className={styles.metricsGrid}>
                              {entry.correct != null && entry.totalActions != null && (
                                <div className={styles.metricItem}>
                                  <span className={styles.metricIcon}>✅</span>
                                  <span className={styles.metricVal}>{entry.correct}/{entry.totalActions}</span>
                                  <span className={styles.metricLbl}>{translateMetricLabel("Correct", t)}</span>
                                </div>
                              )}
                              {entry.reactionTime != null && entry.reactionTime > 0 && (
                                <div className={styles.metricItem}>
                                  <span className={styles.metricIcon}>⚡</span>
                                  <span className={styles.metricVal}>{Math.round(entry.reactionTime * 1000)}ms</span>
                                  <span className={styles.metricLbl}>{translateMetricLabel("Réaction", t)}</span>
                                </div>
                              )}
                              <div className={styles.metricItem}>
                                <span className={styles.metricIcon}>📊</span>
                                <span className={styles.metricVal}>{t("level.short", { level: entry.level })}</span>
                                <span className={styles.metricLbl}>{translateMetricLabel("Niveau", t)}</span>
                              </div>
                              <div className={styles.metricItem}>
                                <span className={styles.metricIcon}>⏱️</span>
                                <span className={styles.metricVal}>{formatDuration(entry.duration_s)}</span>
                                <span className={styles.metricLbl}>{translateMetricLabel("Durée", t)}</span>
                              </div>
                            </div>

                            <div className={styles.cardMeta}>
                              <span>{formatDate(entry.timestamp, { hour: "2-digit", minute: "2-digit" })}</span>
                              <span>{formatDate(entry.timestamp)}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
