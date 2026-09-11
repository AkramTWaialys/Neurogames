"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { AdminAPI, AdminStats, PopulationData } from "@/lib/adminApi";
import { isSuperAdmin } from "@/lib/adminAuth";
import {
  Activity, Users, Target, Clock,
  AlertCircle, AlertTriangle, Info, Brain
} from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";
import { translateClinicalProfile } from "@/i18n/labels";
import "../admin.css";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const PLOT_LAYOUT_BASE: Record<string, any> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { family: "Inter, system-ui", color: "#94A3B8", size: 11 },
  margin: { l: 50, r: 20, t: 10, b: 50 },
  xaxis: { gridcolor: "rgba(148,163,184,0.08)", zeroline: false },
  yaxis: { gridcolor: "rgba(148,163,184,0.08)", zeroline: false },
  hoverlabel: {
    bgcolor: "#1E293B",
    bordercolor: "transparent",
    font: { family: "Inter", size: 12, color: "#E2E8F0" },
  },
};

const KPI_ICONS = [
  { icon: Activity, labelKey: "common.sessions" },
  { icon: Users, labelKey: "common.participants" },
  { icon: Target, labelKey: "metrics.avgAccuracy" },
  { icon: Clock, labelKey: "metrics.reactionTime" },
];

const SEVERITY_ICON = {
  critical: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

function formatRelativeTime(isoDate: string | null, t: (key: string, params?: Record<string, string | number>) => string): { label: string; status: "fresh" | "stale" | "offline" } {
  if (!isoDate) return { label: t("common.none"), status: "offline" };
  try {
    const d = new Date(isoDate);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffH = Math.floor(diffMs / (1000 * 60 * 60));
    const diffD = Math.floor(diffH / 24);
    let label: string;
    if (diffH < 1) label = t("admin.fresh.lessHour");
    else if (diffH < 24) label = t("admin.fresh.hours", { count: diffH });
    else label = t("admin.fresh.days", { count: diffD, plural: diffD > 1 ? "s" : "" });
    const status = diffD > 7 ? "offline" : diffD > 1 ? "stale" : "fresh";
    return { label, status };
  } catch {
    return { label: t("common.unknown"), status: "offline" };
  }
}

export default function DashboardPage() {
  const { t, formatNumber } = useI18n();
  const router = useRouter();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [population, setPopulation] = useState<PopulationData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (isSuperAdmin()) {
      router.replace("/dashboard/schools");
      return;
    }
    Promise.all([AdminAPI.getStats(), AdminAPI.getPopulation()])
      .then(([s, p]) => {
        setStats(s);
        setPopulation(p);
      })
      .catch(() => setError(true));
  }, [router]);

  const kpiData = stats
    ? [
        { ...KPI_ICONS[0], value: formatNumber(stats.total_sessions) },
        { ...KPI_ICONS[1], value: formatNumber(stats.total_participants) },
        { ...KPI_ICONS[2], value: `${stats.avg_accuracy}%` },
        { ...KPI_ICONS[3], value: `${stats.avg_reaction_time}s` },
      ]
    : null;

  const freshness = stats ? formatRelativeTime(stats.last_updated, t) : null;

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <div className="adminPageHeader">
          <h1>{t("admin.dashboard")}</h1>
          <p>{t("admin.summary")}</p>
        </div>

        {/* Data Freshness Badge */}
        {freshness && (
          <div className="freshnessBadge">
            <span className={`freshnessDot ${freshness.status}`} />
            {t("admin.lastUpdate", { time: freshness.label })}
          </div>
        )}

        {/* Error State */}
        {error ? (
          <div className="loading">
            {t("admin.loadError")}{" "}
            <button
              onClick={() => window.location.reload()}
              style={{
                background: "var(--admin-primary)",
                color: "#fff",
                border: "none",
                borderRadius: "var(--admin-radius-sm)",
                padding: "6px 14px",
                cursor: "pointer",
                fontFamily: "inherit",
                fontWeight: 600,
                fontSize: 13,
              }}
            >
              {t("common.retry")}
            </button>
          </div>
        ) : kpiData ? (
          <div className="kpiGrid">
            {kpiData.map((kpi) => {
              const Icon = kpi.icon;
              return (
                <div className="kpiCard" key={kpi.labelKey}>
                  <span className="kpiIcon"><Icon size={20} /></span>
                  <div className="kpiContent">
                    <h3>{t(kpi.labelKey)}</h3>
                    <div className="kpiValue">{kpi.value}</div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="kpiGrid">
            {[1, 2, 3, 4].map((i) => (
              <div className="kpiCard" key={i} style={{ minHeight: 60, opacity: 0.5, animation: "pulse 1.5s ease-in-out infinite" }}>
                <span className="kpiIcon" style={{ opacity: 0.3 }} />
                <div className="kpiContent">
                  <h3 style={{ width: 80, height: 10, background: "var(--admin-border)", borderRadius: 4 }}>&nbsp;</h3>
                  <div style={{ width: 60, height: 20, background: "var(--admin-border)", borderRadius: 4, marginTop: 4 }} />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Alerts Panel */}
        {stats && stats.alerts && stats.alerts.length > 0 && (
          <div className="alertsPanel">
            {stats.alerts.map((alert, i) => {
              const SevIcon = SEVERITY_ICON[alert.severity] || Info;
              const iconColor = alert.severity === "critical" ? "#EF4444" : alert.severity === "warning" ? "#EAB308" : "#0891B2";
              return (
                <div key={i} className={`alertCard ${alert.severity}`}>
                  <span className="alertIcon"><SevIcon size={16} color={iconColor} /></span>
                  <div className="alertBody">
                    <div className="alertTitle">{alert.title}</div>
                    <div className="alertDetail">{alert.detail}</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* At-Risk Participants */}
        {stats && stats.at_risk_participants && stats.at_risk_participants.length > 0 && (
          <div className="chartCard" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "16px 20px 8px" }}>
              <h2 style={{ margin: 0 }}>{t("admin.atRisk", { count: stats.at_risk_participants.length })}</h2>
            </div>
            <table className="dataTable">
              <thead>
                <tr>
                  <th>{t("common.participant")}</th>
                  <th>{t("admin.anomalies")}</th>
                  <th>{t("admin.trend")}</th>
                  <th>{t("admin.reason")}</th>
                </tr>
              </thead>
              <tbody>
                {stats.at_risk_participants.map((p) => (
                  <tr key={p.participant_id}>
                    <td style={{ fontFamily: "monospace, Inter", fontSize: 12 }}>{p.participant_id}</td>
                    <td style={{ color: p.anomaly_rate ? "#EF4444" : "#94A3B8", fontWeight: 600 }}>
                      {p.anomaly_rate || "—"}
                    </td>
                    <td style={{ color: p.trend ? "#EAB308" : "#94A3B8", fontWeight: 600 }}>
                      {p.trend || "—"}
                    </td>
                    <td style={{ fontSize: 12, color: "#94A3B8" }}>{p.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Population Trends — Merged from Analysis */}
        {population && population.profiles.length > 0 && (
          <div className="chartCard">
            <h2>{t("admin.adhdDistribution")}</h2>
            <Plot
              data={[
                {
                  type: "pie",
                  hole: 0.5,
                  labels: population.profiles.map((p) => translateClinicalProfile(p.profile, t)),
                  values: population.profiles.map((p) => p.count),
                  marker: { colors: ["#86EFAC", "#FDE68A", "#FCA5A5", "#C4B5FD", "#94A3B8"] },
                  textinfo: "label+percent",
                  textfont: { color: "#E2E8F0", size: 11, family: "Inter" },
                  hovertemplate: `%{label}: %{value} ${t("common.sessions")} (%{percent})<extra></extra>`,
                },
              ]}
              layout={{
                ...PLOT_LAYOUT_BASE,
                height: 320,
                showlegend: true,
                legend: { font: { color: "#94A3B8", family: "Inter", size: 11 }, orientation: "h" as const, y: -0.1 },
                margin: { l: 20, r: 20, t: 10, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", minHeight: '320px' }}
            />
          </div>
        )}

        {population && population.daily_sessions.length > 0 && (
          <div className="chartCard">
            <h2>{t("admin.sessionsPerDay")}</h2>
            <Plot
              data={[
                {
                  type: "scatter",
                  mode: "lines",
                  fill: "tozeroy",
                  x: population.daily_sessions.map((d) => d.date),
                  y: population.daily_sessions.map((d) => d.sessions),
                  line: { color: "#7C3AED", width: 2 },
                  fillcolor: "rgba(124, 58, 237, 0.1)",
                  hovertemplate: `%{x}: %{y} ${t("common.sessions")}<extra></extra>`,
                },
              ]}
              layout={{
                ...PLOT_LAYOUT_BASE,
                height: 260,
                xaxis: { ...PLOT_LAYOUT_BASE.xaxis },
                yaxis: { ...PLOT_LAYOUT_BASE.yaxis, title: { text: t("common.sessions"), font: { size: 11 } } },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%", minHeight: '260px' }}
            />
          </div>
        )}

        {/* Call to Action: Detailed Analysis */}
        <div style={{ marginTop: '32px', textAlign: 'center', padding: '40px', background: 'var(--admin-card)', borderRadius: '12px', border: '1px solid var(--admin-border)' }}>
          <Brain size={48} style={{ color: 'var(--admin-primary)', marginBottom: '16px', opacity: 0.8 }} />
          <h2 style={{ margin: '0 0 8px' }}>{t("admin.explorePerformance")}</h2>
          <p style={{ color: 'var(--admin-text-muted)', marginBottom: '24px', maxWidth: '400px', marginInline: 'auto' }}>
            {t("admin.explorePerformanceDesc")}
          </p>
          <button 
            onClick={() => router.push("/dashboard/analysis")}
            className="reportGenerateBtn"
          >
            {t("admin.explore")} →
          </button>
        </div>
      </main>
    </div>
  );
}
