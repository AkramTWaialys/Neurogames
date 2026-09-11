"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useParams } from "next/navigation";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  Activity,
  BarChart3,
  Brain,
  Calendar,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  GraduationCap,
  School,
  User,
} from "lucide-react";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { AdminAPI, TimelinePoint, ConcordanceRecord, AdminPlayerProfile } from "@/lib/adminApi";
import { NeuroAPI } from "@/lib/api";
import type { ParticipantSummary, PlayerLookup } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";
import { translateClinicalProfile, translateConnersTier, translateGameName, translatePerformanceLevel } from "@/i18n/labels";
import "../../admin.css";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const PLOT_BASE: Record<string, any> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { family: "Inter, system-ui", color: "#94A3B8", size: 12 },
  margin: { l: 50, r: 50, t: 10, b: 50 },
};

const GAME_ICONS: Record<string, string> = {
  gonogo: "🎯", memory: "🃏", tracking: "👁️", shapes: "🔷", puzzle: "🧩",
};

/* ── Report Types ─────────────────────────────────────────────── */

interface ComparisonWindow {
  recent_sessions: number;
  baseline_sessions: number;
  total_sessions: number;
}

interface GameData {
  cognitive_domain: string;
  total_sessions: number;
  severity: "normal" | "mild" | "moderate" | "severe";
  clinical_cluster: string;
  narrative: string;
  improvement_narrative: string;
  cluster_narrative: string;
  comparison_window: ComparisonWindow | null;
  highest_level_reached: number | null;
  accuracy_rate: number | null;
  avg_reaction_time?: number | null;
  avg_frustration_clicks: number;
}

interface OverallData {
  total_sessions: number;
  games_played: number;
  primary_cluster: string;
  worst_severity: string;
  narrative_summary: string;
}

interface StructuredReport {
  participant_id: string;
  generated_at: string;
  report_type: string;
  games: { [gameId: string]: GameData };
  overall: OverallData;
  recommendations?: string[];
}

interface ReportResponse {
  ok: boolean;
  reportText: string;
  method: string;
  structured: StructuredReport | null;
  validation: { valid: boolean; issues: string[] };
  error?: string;
}

interface ReportStatus {
  totalSessions: number;
  hasReport: boolean;
  reportDate: string | null;
  sessionsSinceReport: number;
  eligible: boolean;
  reason: string | null;
}

/* ── Report Constants ─────────────────────────────────────────── */

const GAME_META: Record<string, { icon: string; color: string }> = {
  gonogo: { icon: "🎯", color: "#6C5CE7" },
  memory: { icon: "🃏", color: "#00B894" },
  tracking: { icon: "👁️", color: "#FDCB6E" },
  shapes: { icon: "🔷", color: "#E17055" },
  puzzle: { icon: "🧩", color: "#0984E3" },
};

const GAME_ORDER = Object.keys(GAME_META);

const formatPercent = (value: number | null | undefined) => (
  value == null ? "N/A" : `${Math.round(value * 100)}%`
);

const formatMetric = (value: number | null | undefined, suffix = "") => (
  value == null ? "N/A" : `${value}${suffix}`
);

const SEVERITY_CONFIG: Record<string, { labelKey: string; color: string; bg: string; icon: string }> = {
  normal: { labelKey: "report.statusOnTrack", color: "#059669", bg: "rgba(5,150,105,0.15)", icon: "✅" },
  mild: { labelKey: "report.statusMonitor", color: "#D97706", bg: "rgba(217,119,6,0.15)", icon: "🟡" },
  moderate: { labelKey: "report.statusNeedsSupport", color: "#EA580C", bg: "rgba(234,88,12,0.15)", icon: "🟠" },
  severe: { labelKey: "report.statusAttention", color: "#DC2626", bg: "rgba(220,38,38,0.15)", icon: "🔴" },
};

const SESSION_THRESHOLD = 20;
const HISTORY_PREVIEW_SIZE = 10;
const HISTORY_PAGE_SIZE = 20;

/* ── Main Component ───────────────────────────────────────────── */

export default function ParticipantDetailPage() {
  const { t, formatNumber, formatDate, locale } = useI18n();
  const params = useParams();
  const pid = params.pid as string;

  const [summary, setSummary] = useState<ParticipantSummary | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [playerProfile, setPlayerProfile] = useState<AdminPlayerProfile | null>(null);
  const [playerLookup, setPlayerLookup] = useState<PlayerLookup | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyPage, setHistoryPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Report state
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [reportStatus, setReportStatus] = useState<ReportStatus | null>(null);
  const [generating, setGenerating] = useState(false); // Only true during actual POST /request
  const [reportRetrying, setReportRetrying] = useState(false); // True during transient load failures
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [exportingFhir, setExportingFhir] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryCount = useRef(0);
  const mountedRef = useRef(false);
  const reportRunRef = useRef(0);

  // Concordance state
  const [concordance, setConcordance] = useState<ConcordanceRecord | null>(null);

  const clearReportTimers = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (retryRef.current) {
      clearInterval(retryRef.current);
      retryRef.current = null;
    }
  }, []);

  const isReportRunActive = useCallback((runId: number) => (
    mountedRef.current && reportRunRef.current === runId
  ), []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      reportRunRef.current += 1;
      clearReportTimers();
    };
  }, [clearReportTimers]);

  // Poll task status
  const startPolling = useCallback((taskId: string, runId = reportRunRef.current) => {
    if (!isReportRunActive(runId)) return;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }

    pollRef.current = setInterval(async () => {
      if (!isReportRunActive(runId)) {
        clearReportTimers();
        return;
      }

      const result = await NeuroAPI.pollTaskStatus(taskId);
      if (!isReportRunActive(runId) || !result.ok) return;

      if (result.state === "success") {
        clearReportTimers();
        if (!isReportRunActive(runId)) return;
        setGenerating(false);

        const [newReport, newStatus] = await Promise.all([
          NeuroAPI.getLatestReport(pid, locale),
          NeuroAPI.getReportStatus(pid),
        ]);
        if (!isReportRunActive(runId)) return;
        if (newReport.ok) {
          setReport({
            ok: true,
            reportText: newReport.reportText,
            method: newReport.method,
            structured: newReport.structured as StructuredReport | null,
            validation: { valid: true, issues: [] },
          });
          setReportError(null);
        }
        if (newStatus.ok) setReportStatus(newStatus);
      } else if (result.state === "failed") {
        clearReportTimers();
        if (!isReportRunActive(runId)) return;
        setGenerating(false);
        setReportError(result.error || t("report.unavailable"));
      }
    }, 5000);
  }, [clearReportTimers, isReportRunActive, locale, pid, t]);

  // Retry polling for transient load failures (NOT for starting new generation)
  const startRetryPolling = useCallback((runId = reportRunRef.current) => {
    if (!isReportRunActive(runId)) return;
    if (retryRef.current) {
      clearInterval(retryRef.current);
      retryRef.current = null;
    }
    retryCount.current = 0;
    setReportRetrying(true);

    retryRef.current = setInterval(async () => {
      if (!isReportRunActive(runId)) {
        clearReportTimers();
        return;
      }
      retryCount.current += 1;
      if (retryCount.current > 8) {
        clearReportTimers();
        if (!isReportRunActive(runId)) return;
        setReportRetrying(false);
        setGenerating(false);
        setReportError("no_report");
        return;
      }

      const [newReport, newStatus] = await Promise.all([
        NeuroAPI.getLatestReport(pid, locale),
        NeuroAPI.getReportStatus(pid),
      ]);

      if (!isReportRunActive(runId)) return;
      if (newReport.ok) {
        clearReportTimers();
        setReportRetrying(false);
        setReport({
          ok: true,
          reportText: newReport.reportText,
          method: newReport.method,
          structured: newReport.structured as StructuredReport | null,
          validation: { valid: true, issues: [] },
        });
        setReportError(null);
      }
      if (newStatus.ok) setReportStatus(newStatus);
    }, 15000);
  }, [clearReportTimers, isReportRunActive, locale, pid]);

  // Load data
  useEffect(() => {
    if (!pid) return;
    let cancelled = false;
    const normalizedPid = String(pid).trim().toLowerCase();

    Promise.all([
      NeuroAPI.getSummary(pid),
      AdminAPI.getTimeline(pid).catch(() => null),
      AdminAPI.getParticipantConcordance(pid).catch(() => null),
      AdminAPI.getPlayerProfiles().catch(() => null),
      NeuroAPI.lookupPlayer(pid),
    ]).then(([sumRes, tlRes, concRes, profilesRes, lookupRes]) => {
      if (cancelled || !mountedRef.current) return;
      if (sumRes.ok && sumRes.data) setSummary(sumRes.data);
      if (tlRes) setTimeline(tlRes.timeline);
      if (concRes) setConcordance(concRes);
      if (profilesRes?.players) {
        setPlayerProfile(
          profilesRes.players.find((player) => (
            String(player.username).trim().toLowerCase() === normalizedPid ||
            String(player.id).trim().toLowerCase() === normalizedPid
          )) || null
        );
      }
      if (lookupRes.ok && lookupRes.data) setPlayerLookup(lookupRes.data);
      setLoading(false);
    }).catch(() => {
      if (cancelled || !mountedRef.current) return;
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [pid]);

  // Load report data
  useEffect(() => {
    if (!pid) return;
    const runId = reportRunRef.current + 1;
    reportRunRef.current = runId;
    clearReportTimers();
    let cancelled = false;

    const loadReport = async () => {
      if (cancelled || !isReportRunActive(runId)) return;
      setReportLoading(true);
      setReportError(null);

      const [statusResult, reportResult] = await Promise.all([
        NeuroAPI.getReportStatus(pid),
        NeuroAPI.getLatestReport(pid, locale),
      ]);

      if (cancelled || !isReportRunActive(runId)) return;
      if (statusResult.ok) setReportStatus(statusResult);

      if (reportResult.ok) {
        setReport({
          ok: true,
          reportText: reportResult.reportText,
          method: reportResult.method,
          structured: reportResult.structured as StructuredReport | null,
          validation: { valid: true, issues: [] },
        });
      } else if (reportResult.error === "no_report") {
        setReportError("no_report");
      } else {
        startRetryPolling(runId);
      }

      // Check if a report is currently being generated via the queue
      const queueResult = await NeuroAPI.getQueueStatus(pid);
      if (cancelled || !isReportRunActive(runId)) return;
      if (queueResult.ok && queueResult.generating && queueResult.taskId) {
        if (retryRef.current) {
          clearInterval(retryRef.current);
          retryRef.current = null;
        }
        setGenerating(true);
        startPolling(queueResult.taskId, runId);
      }

      setReportLoading(false);
    };

    void Promise.resolve().then(loadReport);
    return () => {
      cancelled = true;
      if (reportRunRef.current === runId) {
        reportRunRef.current += 1;
      }
      clearReportTimers();
    };
  }, [clearReportTimers, isReportRunActive, locale, pid, startPolling, startRetryPolling]);

  // Request new report
  const handleRequestReport = async () => {
    const runId = reportRunRef.current + 1;
    reportRunRef.current = runId;
    clearReportTimers();
    setReportError(null);
    setGenerating(true);

    const result = await NeuroAPI.requestReport(pid, locale);
    if (!isReportRunActive(runId)) return;
    if (!result.ok) {
      setGenerating(false);
      setReportError(result.error || t("report.unavailable"));
      return;
    }
    if (result.taskId) {
      startPolling(result.taskId, runId);
    } else {
      setGenerating(false);
    }
  };

  const handleExportFhir = async () => {
    setReportError(null);
    setExportingFhir(true);
    try {
      const response = await AdminAPI.downloadFhirBundle(pid);
      if (!response.ok) {
        throw new Error(`FHIR export failed (HTTP ${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `neurogames-fhir-${pid}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (!mountedRef.current) return;
      setReportError(error instanceof Error ? error.message : "FHIR export failed");
    } finally {
      if (!mountedRef.current) return;
      setExportingFhir(false);
    }
  };

  // Report helpers
  const s = report?.structured;
  const hasStructured = s && Object.keys(s.games).length > 0;
  const gameEntries = s
    ? [
        ...GAME_ORDER.filter((gameId) => s.games[gameId]).map((gameId) => [gameId, s.games[gameId]] as [string, GameData]),
        ...Object.entries(s.games).filter(([gameId]) => !GAME_ORDER.includes(gameId)),
      ]
    : [];

  const playerName = playerProfile?.display_name || playerProfile?.username || summary?.participant_id || pid;
  const playerAge = playerProfile?.age ?? playerLookup?.age ?? null;
  const playerSchool = playerProfile?.school_name || playerProfile?.custom_school_name || t("common.na");
  const joinedDate = playerProfile?.created_at || null;
  const clinicalComparisonLabel = concordance
    ? concordance.concordance === "concordant"
      ? t("admin.concordant")
      : concordance.concordance === "partial"
        ? t("admin.partial")
        : t("admin.contradiction")
    : "";
  const clinicalAgreement = concordance?.agreement_ratio != null
    ? `${Math.round(concordance.agreement_ratio * 100)}%`
    : null;
  const connersResultText = concordance
    ? `${translateConnersTier(concordance.conners_tier, t)}${
        concordance.conners_score != null ? ` (${t("admin.connersScore")}: ${concordance.conners_score})` : ""
      }`
    : "";

  const sessionsByGame = useMemo(() => {
    const counts = new Map<string, number>();
    const names = new Map<string, string>();

    if (summary?.games?.length) {
      summary.games.forEach((game) => {
        counts.set(game.game_id, game.sessions_played);
        names.set(game.game_id, game.game_name);
      });
    } else {
      timeline.forEach((row) => {
        counts.set(row.game_id, (counts.get(row.game_id) || 0) + 1);
        names.set(row.game_id, row.game_name);
      });
    }

    const gameIds = [...GAME_ORDER, ...Array.from(counts.keys()).filter((gameId) => !GAME_ORDER.includes(gameId))];
    return gameIds.map((gameId) => ({
      gameId,
      sessions: counts.get(gameId) || 0,
      displayName: names.get(gameId) || gameId,
      meta: GAME_META[gameId] || { icon: "ðŸŽ®", color: "#64748b" },
    }));
  }, [summary, timeline]);
  const totalGameSessions = sessionsByGame.reduce((total, game) => total + game.sessions, 0);

  const sortedTimeline = useMemo(() => (
    [...timeline].sort((a, b) => {
      const parsedA = a.stored_at ? Date.parse(a.stored_at) : 0;
      const parsedB = b.stored_at ? Date.parse(b.stored_at) : 0;
      const at = Number.isFinite(parsedA) ? parsedA : 0;
      const bt = Number.isFinite(parsedB) ? parsedB : 0;
      if (bt !== at) return bt - at;
      return b.session_number - a.session_number;
    })
  ), [timeline]);

  const totalHistoryPages = Math.max(1, Math.ceil(sortedTimeline.length / HISTORY_PAGE_SIZE));
  const safeHistoryPage = Math.min(historyPage, totalHistoryPages);
  const visibleHistory = sortedTimeline.slice(
    (safeHistoryPage - 1) * HISTORY_PAGE_SIZE,
    safeHistoryPage * HISTORY_PAGE_SIZE
  );
  const previewHistory = sortedTimeline.slice(0, HISTORY_PREVIEW_SIZE);
  const historyRows = historyOpen ? visibleHistory : previewHistory;
  const canExpandHistory = sortedTimeline.length > HISTORY_PREVIEW_SIZE;
  const historyStart = sortedTimeline.length === 0 ? 0 : (safeHistoryPage - 1) * HISTORY_PAGE_SIZE + 1;
  const historyEnd = Math.min(safeHistoryPage * HISTORY_PAGE_SIZE, sortedTimeline.length);

  const sessionProgress = reportStatus
    ? Math.min(100, Math.round(
        ((reportStatus.hasReport ? reportStatus.sessionsSinceReport : reportStatus.totalSessions) / SESSION_THRESHOLD) * 100
      ))
    : 0;

  if (loading) {
    return (
      <div className="adminShell">
        <AdminSidebar />
        <main className="adminMain">
          <div className="loading">{t("common.loading")}</div>
        </main>
      </div>
    );
  }

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain participantDetailMain">
        <Link href="/dashboard/participants" className="backLink">
          ← {t("admin.backParticipants")}
        </Link>

        <section className="playerProfileSummary">
          <div className="playerProfileIdentity">
            <div className="playerProfileTitle">
              <div className="playerProfileAvatar">
                <User size={24} aria-hidden="true" />
              </div>
              <div>
                <h1>{playerName}</h1>
                <p>{summary?.participant_id || pid}</p>
                {concordance && (
                  <div className="playerClinicalChips" aria-label={t("admin.clinicalSummary")}>
                    <div className="playerClinicalChip">
                      <span>{t("admin.classificationResult")}</span>
                      <strong>{translateClinicalProfile(concordance.ml_prediction, t)}</strong>
                    </div>
                    <div className="playerClinicalChip">
                      <span>{t("admin.connersResult")}</span>
                      <strong>{connersResultText}</strong>
                    </div>
                    <div className={`playerClinicalChip is-${concordance.concordance}`}>
                      <span>{t("admin.comparedToClassification")}</span>
                      <strong>{clinicalComparisonLabel}</strong>
                      {clinicalAgreement && <small>{t("admin.agreement")}: {clinicalAgreement}</small>}
                    </div>
                  </div>
                )}
              </div>
            </div>
            <button className="reportExportBtn" onClick={handleExportFhir} disabled={exportingFhir}>
              <Download size={15} aria-hidden="true" />
              {exportingFhir ? t("admin.exporting") : t("admin.exportFhir")}
            </button>
          </div>

          <div className="playerInfoGrid">
            <div className="playerInfoItem">
              <span><User size={14} aria-hidden="true" /> {t("admin.playerName")}</span>
              <strong>{playerName}</strong>
            </div>
            <div className="playerInfoItem">
              <span><Calendar size={14} aria-hidden="true" /> {t("admin.age")}</span>
              <strong>{playerAge != null ? playerAge : t("common.na")}</strong>
            </div>
            <div className="playerInfoItem">
              <span><School size={14} aria-hidden="true" /> {t("admin.school")}</span>
              <strong>{playerSchool}</strong>
            </div>
            <div className="playerInfoItem">
              <span><BarChart3 size={14} aria-hidden="true" /> {t("admin.totalSessions")}</span>
              <strong>{summary?.total_sessions ?? timeline.length}</strong>
            </div>
            <div className="playerInfoItem">
              <span><Activity size={14} aria-hidden="true" /> {t("metrics.accuracy")}</span>
              <strong>{summary ? `${summary.avg_accuracy}%` : t("common.na")}</strong>
            </div>
            <div className="playerInfoItem">
              <span><GraduationCap size={14} aria-hidden="true" /> {t("metrics.level")}</span>
              <strong>{summary ? translatePerformanceLevel(summary.level, t) : t("common.na")}</strong>
            </div>
            <div className="playerInfoItem">
              <span>XP</span>
              <strong>{summary ? formatNumber(summary.xp) : t("common.na")}</strong>
            </div>
            <div className="playerInfoItem">
              <span>{t("admin.joined")}</span>
              <strong>{joinedDate ? formatDate(joinedDate, { year: "numeric", month: "short", day: "numeric" }) : t("common.na")}</strong>
            </div>
          </div>
        </section>

        <section className="profileInsightsGrid">
          <div className="sessionsCircleCard">
            <div className="sectionTitleRow">
              <h2>{t("report.sessionsByGame")}</h2>
            </div>
            <div className="sessionsDonutWrap">
              {totalGameSessions > 0 ? (
                <>
                  <Plot
                    data={[
                      {
                        type: "pie",
                        labels: sessionsByGame.map(({ gameId, displayName }) => translateGameName(displayName || gameId, t)),
                        values: sessionsByGame.map(({ sessions }) => sessions),
                        hole: 0.48,
                        sort: false,
                        direction: "clockwise",
                        marker: {
                          colors: sessionsByGame.map(({ meta }) => meta.color),
                          line: { color: "#1E293B", width: 2 },
                        },
                        textinfo: "none",
                        hovertemplate: `%{label}<br>%{value} ${t("common.sessions")}<br>%{percent}<extra></extra>`,
                      },
                    ]}
                    layout={{
                      ...PLOT_BASE,
                      height: 230,
                      margin: { l: 6, r: 6, t: 4, b: 4 },
                      showlegend: false,
                      annotations: [
                        {
                          text: `<b>${totalGameSessions}</b><br><span style="font-size:11px;color:#94A3B8">${t("common.sessions")}</span>`,
                          x: 0.5,
                          y: 0.5,
                          showarrow: false,
                          font: { color: "#E2E8F0", size: 18 },
                        },
                      ],
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                  <div className="sessionsDonutLegend">
                    {sessionsByGame.map(({ gameId, sessions, meta, displayName }) => (
                      <div className="sessionsDonutLegendItem" key={gameId}>
                        <span className="sessionsDonutSwatch" style={{ background: meta.color }} />
                        <span>{translateGameName(displayName || gameId, t)}</span>
                        <strong>{sessions}</strong>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="emptyStateSmall">{t("common.none")}</div>
              )}
            </div>
          </div>

          <div className="chartCard performanceEvolutionCard">
            <h2>{t("admin.performanceEvolution")}</h2>
            {timeline.length > 0 ? (
              <Plot
                data={[
                  {
                    type: "scatter",
                    mode: "lines+markers",
                    name: `${t("metrics.accuracy")} (%)`,
                    x: timeline.map((t) => t.session_number),
                    y: timeline.map((t) => t.accuracy),
                    line: { color: "#4ade80", width: 2 },
                    marker: { size: 4 },
                    hovertemplate: `${t("common.sessions")} %{x}: %{y:.1f}%<extra>${t("metrics.accuracy")}</extra>`,
                  },
                  {
                    type: "scatter",
                    mode: "lines+markers",
                    name: `${t("metrics.reactionTime")} (s)`,
                    x: timeline.map((t) => t.session_number),
                    y: timeline.map((t) => t.reaction_time),
                    yaxis: "y2",
                    line: { color: "#7C3AED", width: 2 },
                    marker: { size: 4 },
                    hovertemplate: `${t("common.sessions")} %{x}: %{y:.2f}s<extra>${t("metrics.reactionTime")}</extra>`,
                  },
                ]}
                layout={{
                  ...PLOT_BASE,
                  height: 280,
                  margin: { l: 44, r: 44, t: 8, b: 42 },
                  xaxis: { title: `${t("common.sessions")} #`, gridcolor: "rgba(124,58,237,0.1)" },
                  yaxis: { title: `${t("metrics.accuracy")} (%)`, gridcolor: "rgba(124,58,237,0.1)", range: [0, 100] },
                  yaxis2: { title: "RT (s)", overlaying: "y", side: "right" as const, gridcolor: "rgba(124,58,237,0.05)" },
                  legend: { orientation: "h" as const, y: 1.12, font: { color: "#94A3B8" } },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            ) : (
              <div className="emptyStateSmall">{t("common.none")}</div>
            )}
          </div>
        </section>

        <section className="historyCard">
          <button
            className="historyHeader"
            type="button"
            aria-expanded={historyOpen}
            onClick={() => {
              if (canExpandHistory) setHistoryOpen((open) => !open);
            }}
          >
            <span>
              <strong>{t("admin.sessionHistory")}</strong>
              <small>
                {historyOpen
                  ? `${historyStart}-${historyEnd} / ${sortedTimeline.length}`
                  : `${Math.min(HISTORY_PREVIEW_SIZE, sortedTimeline.length)} / ${sortedTimeline.length}`}
                {" "}{t("common.sessions")} · {t("admin.newestToOldest")}
              </small>
            </span>
            {historyOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
          </button>

          <div className={`tableScroll ${!historyOpen && canExpandHistory ? "historyPreviewTable" : ""}`}>
            <table className="dataTable">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t("admin.game")}</th>
                  <th>{t("admin.date")}</th>
                  <th>{t("metrics.accuracy")}</th>
                  <th>RT</th>
                  <th>{t("metrics.level")}</th>
                  <th>{t("metrics.duration")}</th>
                </tr>
              </thead>
              <tbody>
                {historyRows.map((row) => (
                  <tr key={row.session_number} style={{ cursor: "default" }}>
                    <td>{row.session_number}</td>
                    <td>{GAME_ICONS[row.game_id] || "🎮"} {translateGameName(row.game_id, t)}</td>
                    <td style={{ color: "#94A3B8" }}>
                      {row.stored_at ? formatDate(row.stored_at, { year: "numeric", month: "short", day: "numeric" }) : t("common.na")}
                    </td>
                    <td>{row.accuracy}%</td>
                    <td>{row.reaction_time != null ? `${row.reaction_time.toFixed(2)}s` : t("common.na")}</td>
                    <td>{row.level}</td>
                    <td>{row.duration_s.toFixed(0)}s</td>
                  </tr>
                ))}
                {historyRows.length === 0 && (
                  <tr>
                    <td colSpan={7} style={{ textAlign: "center", color: "#94A3B8" }}>
                      {t("common.none")}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            {!historyOpen && canExpandHistory && (
              <div className="historyPreviewFade">
                <button
                  className="historyExpandBtn"
                  type="button"
                  onClick={() => setHistoryOpen(true)}
                >
                  {t("admin.showAllSessions")}
                  <ChevronDown size={16} aria-hidden="true" />
                </button>
              </div>
            )}
          </div>

          {historyOpen && (
            <>
              <div className="historyPagination">
                <span className="pageInfo">
                  {historyStart}-{historyEnd} / {sortedTimeline.length}
                </span>
                <div className="historyPageButtons">
                  <button
                    className="pageBtn"
                    type="button"
                    onClick={() => setHistoryOpen(false)}
                  >
                    {t("admin.collapse")}
                    <ChevronUp size={14} aria-hidden="true" />
                  </button>
                  <button
                    className="pageBtn iconPageBtn"
                    type="button"
                    aria-label={t("admin.previousSessions", { count: HISTORY_PAGE_SIZE })}
                    disabled={safeHistoryPage <= 1}
                    onClick={() => setHistoryPage((page) => Math.max(1, page - 1))}
                  >
                    <ChevronLeft size={16} aria-hidden="true" />
                  </button>
                  <span className="pageInfo">
                    {t("common.page", { page: safeHistoryPage, total: totalHistoryPages })}
                  </span>
                  <button
                    className="pageBtn iconPageBtn"
                    type="button"
                    aria-label={t("admin.nextSessions", { count: HISTORY_PAGE_SIZE })}
                    disabled={safeHistoryPage >= totalHistoryPages}
                    onClick={() => setHistoryPage((page) => Math.min(totalHistoryPages, page + 1))}
                  >
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                </div>
              </div>
            </>
          )}
        </section>

        {/* ═══════════════════════════════════════════════════════════════
            Conners Concordance Card
           ═══════════════════════════════════════════════════════════════ */}
        {concordance && (
          <div className="chartCard concordanceCard" style={{ marginTop: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <span style={{ fontSize: 20 }}>⚖️</span>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{t("admin.connersConcordance")}</h2>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginBottom: 16 }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t("admin.mlPrediction")}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#E2E8F0" }}>{translateClinicalProfile(concordance.ml_prediction, t)}</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t("admin.connersTier")}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#E2E8F0" }}>{translateConnersTier(concordance.conners_tier, t)}</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t("admin.connersScore")}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#E2E8F0" }}>{concordance.conners_score ?? "—"}</div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t("admin.status")}</div>
                <span style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 12px",
                  borderRadius: 20,
                  fontSize: 12,
                  fontWeight: 700,
                  background: concordance.concordance === "concordant" ? "rgba(6,214,160,0.15)" :
                              concordance.concordance === "partial" ? "rgba(255,209,102,0.15)" : "rgba(239,71,111,0.15)",
                  color: concordance.concordance === "concordant" ? "#06D6A0" :
                         concordance.concordance === "partial" ? "#FFD166" : "#EF476F",
                }}>
                  {concordance.concordance === "concordant" ? "✅" : concordance.concordance === "partial" ? "⚠️" : "🔴"}
                  {" "}{concordance.concordance === "concordant" ? t("admin.concordant") : concordance.concordance === "partial" ? t("admin.partial") : t("admin.contradiction")}
                </span>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 11, color: "#94A3B8", marginBottom: 4 }}>{t("admin.confidence")}</div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  <span style={{ color: "#64748B" }}>
                    {concordance.original_confidence != null ? `${(concordance.original_confidence * 100).toFixed(0)}%` : "—"}
                  </span>
                  <span style={{ color: "#475569", margin: "0 4px" }}>→</span>
                  <span style={{
                    color: concordance.concordance === "concordant" ? "#06D6A0" :
                           concordance.concordance === "partial" ? "#FFD166" : "#EF476F",
                    fontWeight: 700,
                  }}>
                    {concordance.adjusted_confidence != null ? `${(concordance.adjusted_confidence * 100).toFixed(0)}%` : "—"}
                  </span>
                </div>
              </div>
            </div>
            {concordance.correction_flag && (
              <div style={{
                padding: "10px 14px",
                borderRadius: 8,
                background: "rgba(239,71,111,0.08)",
                border: "1px solid rgba(239,71,111,0.2)",
                fontSize: 13,
                color: "#FCA5A5",
                lineHeight: 1.5,
              }}>
                ⚠️ {concordance.correction_flag}
              </div>
            )}
          </div>
        )}

        {/* ═══════════════════════════════════════════════════════════════
            Clinical Report Section
           ═══════════════════════════════════════════════════════════════ */}
        <div className="reportSection compactReportSection">
          <div className="reportSectionHeader">
            <h2><Brain size={18} aria-hidden="true" /> {t("admin.cognitivePerformanceReport")}</h2>
          </div>

          {/* Report Status Bar */}
          {reportStatus && !reportLoading && (
            <div className="reportStatusBar">
              <div className="reportStatusInfo">
                <div className="reportStatusRow">
                  <span className="reportStatusLabel">{t("report.totalSessions")}</span>
                  <span className="reportStatusValue">{reportStatus.totalSessions}</span>
                </div>
                {reportStatus.hasReport && reportStatus.reportDate && (
                  <div className="reportStatusRow">
                    <span className="reportStatusLabel">{t("report.lastReport")}</span>
                    <span className="reportStatusValue">
                      {formatDate(reportStatus.reportDate, { year: "numeric", month: "short", day: "numeric" })}
                    </span>
                  </div>
                )}
                <div className="reportStatusRow">
                  <span className="reportStatusLabel">
                    {reportStatus.hasReport ? t("report.sessionsSince") : t("report.sessionsPlayed")}
                  </span>
                  <span className="reportStatusValue">
                    {reportStatus.hasReport ? reportStatus.sessionsSinceReport : reportStatus.totalSessions} / {SESSION_THRESHOLD}
                  </span>
                </div>
              </div>

              {/* Progress bar */}
              <div className="reportProgressContainer">
                <div className="reportProgressBar" style={{ width: `${sessionProgress}%` }} />
              </div>

              {/* Generate button or status */}
              <div className="reportActions">
                {generating ? (
                  <div className="reportGenerating">
                    <div className="reportSpinner" />
                    <span>{t("report.generating")}</span>
                  </div>
                ) : reportRetrying ? (
                  <div className="reportGenerating">
                    <div className="reportSpinner" />
                    <span>{t("common.loading")}...</span>
                  </div>
                ) : reportStatus.eligible ? (
                  <button className="reportGenerateBtn" onClick={handleRequestReport} disabled={generating || reportRetrying}>
                    📊 {t("actions.requestReport")}
                  </button>
                ) : (
                  <div className="reportNotEligible">
                    {(() => {
                      const played = reportStatus.hasReport ? reportStatus.sessionsSinceReport : reportStatus.totalSessions;
                      const remaining = Math.max(0, SESSION_THRESHOLD - played);
                      return remaining > 0
                        ? t("report.playMore", { count: remaining, plural: remaining !== 1 ? "s" : "" })
                        : (reportStatus.reason || t("report.almost"));
                    })()}
                  </div>
                )}
                {reportError && reportError !== "no_report" && (
                  <div className="reportRequestError">⚠️ {reportError}</div>
                )}
              </div>
            </div>
          )}

          {/* Report Loading */}
          {reportLoading && (
            <div className="loading">{t("report.loading")}</div>
          )}

          {/* No report placeholder */}
          {!reportLoading && reportError === "no_report" && !report && (
            <div className="reportPlaceholder">
              <span className="reportPlaceholderIcon">📊</span>
              <h3>{t("report.preparingTitle")}</h3>
              <p>
                {t("report.preparingBody", { threshold: SESSION_THRESHOLD })}
                {reportStatus && reportStatus.totalSessions > 0 && (
                  <> {t("report.playedSoFar", { count: reportStatus.totalSessions, plural: reportStatus.totalSessions !== 1 ? "s" : "" })}</>
                )}
              </p>
            </div>
          )}

          {/* ── Structured Report Display ──────────────────────────── */}
          {report && hasStructured && s && (
            <div className="reportDisplay reportDisplayCompact">
              {/* Overall summary card */}
              <div className="reportSummaryCard">
                <div className="reportSummaryHeader">
                  <span className="reportSummaryIcon">🧠</span>
                  <div>
                    <h3>{t("report.profileTitle")}</h3>
                    <span className="reportSummaryDate">
                      {s.generated_at ? formatDate(s.generated_at, { year: "numeric", month: "long", day: "numeric" }) : ""}
                    </span>
                  </div>
                </div>

                <div className="reportNarrative">
                  {s.overall.narrative_summary || t("report.defaultSummary", { sessions: s.overall.total_sessions, games: s.overall.games_played })}
                </div>

                <div className="reportOverviewGrid">
                  <div className="reportOverviewItem">
                    <span className="reportOverviewVal">{s.overall.total_sessions}</span>
                    <span className="reportOverviewLbl">{t("common.sessions")}</span>
                  </div>
                  <div className="reportOverviewItem">
                    <span className="reportOverviewVal">{s.overall.games_played}</span>
                    <span className="reportOverviewLbl">{t("metrics.gamesIncluded")}</span>
                  </div>
                  <div className="reportOverviewItem">
                    <span className="reportOverviewVal">{s.report_type || t("nav.report")}</span>
                    <span className="reportOverviewLbl">{t("metrics.reportType")}</span>
                  </div>
                  <div className="reportOverviewItem">
                    <span className="reportOverviewVal">{s.overall.primary_cluster || t("common.unknown")}</span>
                    <span className="reportOverviewLbl">{t("metrics.profileSignal")}</span>
                  </div>
                </div>

                {/* Severity badge */}
                <div className="reportSeverityBadge" style={{
                  background: SEVERITY_CONFIG[s.overall.worst_severity]?.bg || "rgba(5,150,105,0.15)",
                  color: SEVERITY_CONFIG[s.overall.worst_severity]?.color || "#059669",
                }}>
                  {SEVERITY_CONFIG[s.overall.worst_severity]?.icon || "✅"}{" "}
                  {t(SEVERITY_CONFIG[s.overall.worst_severity]?.labelKey || "report.statusOnTrack")}
                </div>
              </div>

              {/* Per-game narrative cards */}
              <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 14, color: "var(--admin-text)" }}>
                {t("report.observations")}
              </h3>

              <div className="reportGameGrid">
                {gameEntries.map(([gameId, gd]) => {
                  const meta = GAME_META[gameId] || { icon: "🎮", color: "#6C5CE7" };
                  const sev = SEVERITY_CONFIG[gd.severity] || SEVERITY_CONFIG.normal;

                  return (
                    <div key={gameId} className="reportGameCard" style={{ borderTopColor: meta.color }}>
                      {/* Header */}
                      <div className="reportGameHeader">
                        <span className="reportGameIcon">{meta.icon}</span>
                        <div className="reportGameHeaderText">
                          <h4>{translateGameName(gameId, t)}</h4>
                          <span className="reportGameDomain">{t(`games.${gameId}.description`) || gd.cognitive_domain}</span>
                        </div>
                        <span className="reportGameSeverity" style={{ background: sev.bg, color: sev.color }}>
                          {sev.icon} {t(sev.labelKey)}
                        </span>
                      </div>

                      {/* Metric strip */}
                      <div className="reportGameMetrics">
                        <div className="reportGameMetric">
                          <span>{t("common.sessions")}</span>
                          <strong>{gd.total_sessions}</strong>
                        </div>
                        <div className="reportGameMetric">
                          <span>{t("metrics.accuracy")}</span>
                          <strong>{formatPercent(gd.accuracy_rate)}</strong>
                        </div>
                        <div className="reportGameMetric">
                          <span>{t("metrics.level")}</span>
                          <strong>{formatMetric(gd.highest_level_reached)}</strong>
                        </div>
                        <div className="reportGameMetric">
                          <span>{t("metrics.frustration")}</span>
                          <strong>{formatMetric(gd.avg_frustration_clicks)}</strong>
                        </div>
                      </div>

                      {/* Narrative */}
                      <div className="reportGameNarrative">
                        {gd.narrative || t("report.noInterpretation")}
                      </div>

                      {/* Improvement curve */}
                      {gd.improvement_narrative && (
                        <div className="reportImprovement">
                          <div className="reportImprovementTitle">{t("report.learningCurve")}</div>
                          <div className="reportGameNarrative">{gd.improvement_narrative}</div>
                          {gd.comparison_window && (
                            <div className="reportComparisonNote">
                              {t("report.comparisonNote", { recent: gd.comparison_window.recent_sessions, baseline: gd.comparison_window.baseline_sessions })}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Recommendations */}
              {s.recommendations && s.recommendations.length > 0 && (
                <div className="reportRecommendations">
                  <h3>{t("report.nextSteps")}</h3>
                  <ol>
                    {s.recommendations.map((rec, i) => (
                      <li key={`${rec}-${i}`}>{rec}</li>
                    ))}
                  </ol>
                </div>
              )}

              {/* Method badge */}
              {report.method && (
                <div className="reportMethodBadge">
                  {report.method === "llm" ? t("report.method.llm") :
                    report.method === "pre_generated" ? t("report.method.preGenerated") :
                      report.method === "cached" ? t("report.method.cached") :
                        t("report.method.auto")}
                </div>
              )}
            </div>
          )}

          {/* Fallback: raw markdown if no structured data */}
          {report && !hasStructured && report.reportText && (
            <div className="chartCard">
              <div
                dangerouslySetInnerHTML={{
                  __html: report.reportText
                    .replace(/^### (.*$)/gm, "<h3>$1</h3>")
                    .replace(/^## (.*$)/gm, "<h2>$1</h2>")
                    .replace(/^# (.*$)/gm, "<h1>$1</h1>")
                    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
                    .replace(/\*(.*?)\*/g, "<em>$1</em>")
                    .replace(/\n/g, "<br/>")
                }}
              />
              {report.method && (
                <div className="reportMethodBadge">
                  {report.method === "llm" ? t("report.method.llm") : t("report.method.auto")}
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
