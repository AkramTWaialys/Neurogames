"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import AdminSidebar from "@/components/admin/AdminSidebar";
import {
  AdminAPI,
  GameModule,
  GameAnalytics,
  AnomalyData,
  ClassificationData,
  ConcordanceData,
} from "@/lib/adminApi";
import { isSuperAdmin } from "@/lib/adminAuth";
import {
  Target, Clock, AlertTriangle, Brain,
  Activity, ShieldCheck, Scale, CheckCircle2, XCircle, TrendingUp
} from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";
import { translateClinicalProfile, translateConnersTier, translateGameName } from "@/i18n/labels";
import "../admin.css";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

type PlotAxis = Record<string, unknown>;
type PlotLayoutBase = Record<string, unknown> & { xaxis: PlotAxis; yaxis: PlotAxis };

const PLOT_BASE: PlotLayoutBase = {
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

const PROFILE_COLORS: Record<string, string> = {
  "Optimal / Neurotypical": "#06D6A0",
  "Inattentive ADHD": "#FFD166",
  "Hyperactive-Impulsive ADHD": "#EF476F",
  "Combined ADHD": "#118AB2",
  "Unknown": "#94A3B8",
};

type TFunction = (key: string) => string;
type ConcordanceStyle = { bg: string; color: string; icon: ReactNode; label: string };

const getConcordanceStyles = (t: TFunction): Record<string, ConcordanceStyle> => ({
  concordant: { bg: "rgba(6,214,160,0.12)", color: "#06D6A0", icon: <CheckCircle2 size={14} />, label: t("admin.concordant") },
  partial:    { bg: "rgba(255,209,102,0.12)", color: "#FFD166", icon: <AlertTriangle size={14} />, label: t("admin.partial") },
  discordant: { bg: "rgba(239,71,111,0.12)", color: "#EF476F", icon: <XCircle size={14} />, label: t("admin.contradiction") },
});

const BUILTIN_GAMES = [
  { id: "gonogo" },
  { id: "memory" },
  { id: "tracking" },
  { id: "shapes" },
  { id: "puzzle" },
];

type AnalysisGameOption = { id: string; displayName?: string | null };

type Tab = "per_game" | "classification" | "anomalies" | "concordance";

export default function AnalysisPage() {
  const { t, formatNumber } = useI18n();
  const [tab, setTab] = useState<Tab>("per_game");
  const [analysisGames, setAnalysisGames] = useState<AnalysisGameOption[]>(BUILTIN_GAMES);

  // Per-Game Tab
  const [selectedGame, setSelectedGame] = useState("gonogo");
  const [gameData, setGameData] = useState<GameAnalytics | null>(null);
  
  // Classification Tab
  const [classGame, setClassGame] = useState("gonogo");
  const [classData, setClassData] = useState<ClassificationData | null>(null);
  const [classLoading, setClassLoading] = useState(false);

  // Anomalies Tab
  const [anomalyGame, setAnomalyGame] = useState("gonogo");
  const [anomalyData, setAnomalyData] = useState<AnomalyData | null>(null);
  const [anomalyLoading, setAnomalyLoading] = useState(false);

  // Concordance Tab
  const [concordanceData, setConcordanceData] = useState<ConcordanceData | null>(null);
  const [concordanceLoading, setConcordanceLoading] = useState(false);

  const router = useRouter();

  useEffect(() => {
    if (isSuperAdmin()) {
      router.replace("/dashboard/schools");
      return;
    }
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(async () => {
      try {
        const data = await AdminAPI.getGameModules();
        if (cancelled) return;
        const modules = (data?.game_modules || [])
          .filter((module: GameModule) => module.status !== "archived")
          .map((module: GameModule) => ({
            id: module.game_id,
            displayName: module.display_name,
          }));
        if (modules.length > 0) setAnalysisGames(modules);
      } catch (err) {
        console.error(err);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (tab === "per_game") {
      AdminAPI.getGameAnalytics(selectedGame).then(setGameData);
    }
  }, [tab, selectedGame]);

  useEffect(() => {
    if (tab !== "classification") return;
    let cancelled = false;

    Promise.resolve().then(async () => {
      if (cancelled) return;
      setClassLoading(true);
      try {
        const d = await AdminAPI.getClassification(classGame);
        if (!cancelled) setClassData(d);
      } catch (err) {
        console.error(err);
        if (!cancelled) setClassData(null);
      } finally {
        if (!cancelled) setClassLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [tab, classGame]);

  useEffect(() => {
    if (tab !== "anomalies") return;
    let cancelled = false;

    Promise.resolve().then(async () => {
      if (cancelled) return;
      setAnomalyLoading(true);
      try {
        const d = await AdminAPI.getAnomalies(anomalyGame);
        if (!cancelled) setAnomalyData(d);
      } catch (err) {
        console.error(err);
        if (!cancelled) setAnomalyData(null);
      } finally {
        if (!cancelled) setAnomalyLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [tab, anomalyGame]);

  useEffect(() => {
    if (tab !== "concordance") return;
    let cancelled = false;

    Promise.resolve().then(async () => {
      if (cancelled) return;
      setConcordanceLoading(true);
      try {
        const d = await AdminAPI.getConcordance();
        if (!cancelled) setConcordanceData(d);
      } catch (err) {
        console.error(err);
        if (!cancelled) setConcordanceData(null);
      } finally {
        if (!cancelled) setConcordanceLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [tab]);

  // Handle Plotly responsiveness on tab change
  useEffect(() => {
    window.dispatchEvent(new Event('resize'));
  }, [tab]);

  const gameLabel = (game: AnalysisGameOption) =>
    game.displayName || translateGameName(game.id, t);

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <div className="adminPageHeader">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span className="pageIcon" style={{ backgroundColor: 'rgba(99,102,241,0.1)', color: '#6366F1', padding: '10px', borderRadius: '12px' }}>
              <Brain size={24} />
            </span>
            <div>
              <h1>{t("admin.analysis")}</h1>
              <p>{t("admin.analysisExplainer")}</p>
            </div>
          </div>
        </div>

        {/* Tab Bar */}
        <div className="tabBar">
          <button className={`tab ${tab === "per_game" ? "tabActive" : ""}`} onClick={() => setTab("per_game")}>
            {t("admin.perGame")}
          </button>
          <button className={`tab ${tab === "classification" ? "tabActive" : ""}`} onClick={() => setTab("classification")}>
            {t("admin.classification")}
          </button>
          <button className={`tab ${tab === "anomalies" ? "tabActive" : ""}`} onClick={() => setTab("anomalies")}>
            {t("admin.anomalies")}
          </button>
          <button className={`tab ${tab === "concordance" ? "tabActive" : ""}`} onClick={() => setTab("concordance")}>
            {t("admin.concordance")}
          </button>
        </div>



        {/* ── PER-GAME TAB ────────────────────────────────────────────── */}
        {tab === "per_game" && (
          <>
            <select className="adminSelect" value={selectedGame} onChange={(e) => setSelectedGame(e.target.value)}>
              {analysisGames.map((g) => (<option key={g.id} value={g.id}>{gameLabel(g)}</option>))}
            </select>
            {gameData ? (
              <>
                <div className="kpiGrid">
                  <div className="kpiCard">
                    <span className="kpiIcon"><Activity size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("common.sessions")}</h3>
                      <div className="kpiValue">{formatNumber(gameData.total_sessions)}</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><Target size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.avgAccuracy")}</h3>
                      <div className="kpiValue">
                        {(gameData.accuracies.reduce((a, b) => a + b, 0) / Math.max(1, gameData.accuracies.length)).toFixed(1)}%
                      </div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><Clock size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.avgRT")}</h3>
                      <div className="kpiValue">
                        {gameData.reaction_times.length > 0
                          ? (gameData.reaction_times.reduce((a, b) => a + b, 0) / gameData.reaction_times.length).toFixed(2)
                          : "—"}s
                      </div>
                    </div>
                  </div>
                </div>
                <div className="adminDashboardGrid">
                  <div className="chartCard">
                    <h2>{t("admin.accuracyDist")}</h2>
                    <Plot
                      data={[{
                        type: "histogram", x: gameData.accuracies, nbinsx: 30,
                        marker: { color: "#6366F1", opacity: 0.8 },
                      }]}
                      layout={{ ...PLOT_BASE, height: 260 }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", minHeight: '260px' }}
                    />
                  </div>
                  <div className="chartCard">
                    <h2>{t("admin.rtDist")}</h2>
                    <Plot
                      data={[{
                        type: "histogram", x: gameData.reaction_times, nbinsx: 30,
                        marker: { color: "#06B6D4", opacity: 0.8 },
                      }]}
                      layout={{ ...PLOT_BASE, height: 260 }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", minHeight: '260px' }}
                    />
                  </div>
                </div>
              </>
            ) : <div className="loading">{t("common.loading")}</div>}
          </>
        )}

        {/* ── CLASSIFICATION TAB ──────────────────────────────────────── */}
        {tab === "classification" && (
          <>
            <select className="adminSelect" value={classGame} onChange={(e) => setClassGame(e.target.value)}>
              {analysisGames.map((g) => (<option key={g.id} value={g.id}>{gameLabel(g)}</option>))}
            </select>
            {classLoading ? (
              <div className="loading">{t("admin.loadingClassification")}</div>
            ) : classData ? (
              <>
                <div className="kpiGrid">
                  <div className="kpiCard">
                    <span className="kpiIcon"><ShieldCheck size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("metrics.accuracy")}</h3>
                      <div className="kpiValue">{(classData.accuracy * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><Brain size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.avgConfidence")}</h3>
                      <div className="kpiValue">{(classData.avg_confidence * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                </div>
                <div className="adminDashboardGrid">
                  <div className="chartCard">
                    <h2>{t("admin.confusionMatrix")}</h2>
                    <Plot
                      data={[{
                        type: "heatmap",
                        z: classData.classes.map(a => classData.classes.map(p => classData.confusion_matrix[a]?.[p] ?? 0)),
                        x: classData.classes.map((klass) => translateClinicalProfile(klass, t)),
                        y: classData.classes.map((klass) => translateClinicalProfile(klass, t)),
                        colorscale: [[0, "#0F172A"], [1, "#6366F1"]], showscale: false,
                        text: classData.classes.map(a => classData.classes.map(p => String(classData.confusion_matrix[a]?.[p] ?? 0))),
                        texttemplate: "%{text}", textfont: { size: 14, color: "#FFF" }
                      }]}
                      layout={{ ...PLOT_BASE, height: 320, yaxis: { ...PLOT_BASE.yaxis, autorange: "reversed" } }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", minHeight: "320px" }}
                    />
                  </div>
                  <div className="chartCard" style={{ padding: 0 }}>
                    <table className="dataTable">
                      <thead><tr><th>{t("admin.class")}</th><th>{t("admin.precision")}</th><th>{t("admin.recall")}</th><th>F1</th></tr></thead>
                      <tbody>
                        {classData.per_class.map(pc => (
                          <tr key={pc.class}>
                            <td>{translateClinicalProfile(pc.class, t)}</td>
                            <td>{(pc.precision * 100).toFixed(0)}%</td>
                            <td>{(pc.recall * 100).toFixed(0)}%</td>
                            <td>{(pc.f1 * 100).toFixed(0)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            ) : <div className="chartCard"><h2>{t("admin.noClassification")}</h2></div>}
          </>
        )}

        {/* ── ANOMALIES TAB ───────────────────────────────────────────── */}
        {tab === "anomalies" && (
          <>
            <select className="adminSelect" value={anomalyGame} onChange={(e) => setAnomalyGame(e.target.value)}>
              {analysisGames.map((g) => (<option key={g.id} value={g.id}>{gameLabel(g)}</option>))}
            </select>
            {anomalyLoading ? (
              <div className="loading">{t("admin.loadingAnomalies")}</div>
            ) : anomalyData ? (
              <>
                <div className="kpiGrid">
                  <div className="kpiCard">
                    <span className="kpiIcon"><AlertTriangle size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.flagsFound")}</h3>
                      <div className="kpiValue">{anomalyData.combined_flagged}</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><Activity size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.flagRate")}</h3>
                      <div className="kpiValue">{(anomalyData.flag_rate * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                </div>
                <div className="adminDashboardGrid">
                  <div className="chartCard">
                    <h2>{t("admin.anomalyRateProfile")}</h2>
                    <Plot
                      data={anomalyData.profile_rates.map(pr => ({
                        type: "bar", x: [translateClinicalProfile(pr.profile, t)], y: [pr.rate * 100], marker: { color: PROFILE_COLORS[pr.profile] || "#888" }
                      }))}
                      layout={{ ...PLOT_BASE, height: 300, showlegend: false }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", minHeight: "300px" }}
                    />
                  </div>
                  <div className="chartCard">
                    <h2>{t("admin.ifScores")}</h2>
                    <Plot
                      data={[{
                        type: "histogram", x: anomalyData.if_scores, nbinsx: 30,
                        marker: { color: "#6366F1", opacity: 0.8 },
                      }]}
                      layout={{ ...PLOT_BASE, height: 260 }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%", minHeight: "260px" }}
                    />
                  </div>
                </div>
                <div className="chartCard" style={{ marginTop: 24 }}>
                  <h2>{t("admin.aeErrorDist")}</h2>
                  <Plot
                    data={[{
                      type: "histogram", x: anomalyData.ae_errors.map(e => Math.log1p(e)), nbinsx: 40,
                      marker: { color: "#F59E0B", opacity: 0.8 },
                    }]}
                    layout={{ ...PLOT_BASE, height: 260 }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%", minHeight: "260px" }}
                  />
                </div>
                {anomalyData.top_flagged.length > 0 && (
                  <div className="chartCard" style={{ padding: 0, overflow: "hidden", marginTop: 24 }}>
                    <div style={{ padding: "16px 20px" }}>
                      <h2 style={{ margin: 0 }}>{t("admin.flaggedParticipants")}</h2>
                    </div>
                    <table className="dataTable">
                      <thead>
                        <tr><th>ID</th><th>{t("admin.flaggedSessions")}</th><th>{t("admin.totalSessions")}</th><th>{t("admin.rate")}</th></tr>
                      </thead>
                      <tbody>
                        {anomalyData.top_flagged.map((p) => (
                          <tr key={p.participant_id}>
                            <td style={{ fontFamily: "monospace", fontSize: 12 }}>{p.participant_id}</td>
                            <td style={{ color: "#EF4444", fontWeight: 700 }}>{p.flagged_sessions}</td>
                            <td>{p.total_sessions}</td>
                            <td>{(p.rate * 100).toFixed(1)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : <div className="chartCard"><h2>{t("admin.noAnomalies")}</h2></div>}
          </>
        )}

        {/* ── CONCORDANCE TAB ─────────────────────────────────────────── */}
        {tab === "concordance" && (
          <>
            {concordanceLoading ? (
              <div className="loading">{t("admin.loadingConcordance")}</div>
            ) : concordanceData && concordanceData.total_matched > 0 ? (
              <>
                <div className="kpiGrid">
                  <div className="kpiCard">
                    <span className="kpiIcon"><Scale size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.matched")}</h3>
                      <div className="kpiValue">{concordanceData.total_matched}</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><CheckCircle2 size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.agreement")}</h3>
                      <div className="kpiValue">{(concordanceData.concordance_rate * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><TrendingUp size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.avgConfidence")}</h3>
                      <div className="kpiValue">{(concordanceData.avg_adjusted_confidence * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                  <div className="kpiCard">
                    <span className="kpiIcon"><XCircle size={20} /></span>
                    <div className="kpiContent">
                      <h3>{t("admin.contradictions")}</h3>
                      <div className="kpiValue">{concordanceData.n_discordant}</div>
                    </div>
                  </div>
                </div>
                <div className="chartCard" style={{ padding: 0, overflow: 'hidden' }}>
                  <table className="dataTable">
                    <thead>
                      <tr><th>ID</th><th>{t("admin.mlPrediction")}</th><th>{t("admin.connersScore")}</th><th>{t("admin.connersTier")}</th><th>{t("admin.status")}</th><th>{t("admin.flag")}</th></tr>
                    </thead>
                    <tbody>
                      {concordanceData.participants.map(p => {
                        const styles = getConcordanceStyles(t);
                        const style = styles[p.concordance] || styles.discordant;
                        return (
                          <tr key={p.participant_id}>
                            <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{p.participant_id}</td>
                            <td>{translateClinicalProfile(p.ml_prediction, t)}</td>
                            <td>{p.conners_score ?? "—"}</td>
                            <td>{translateConnersTier(p.conners_tier, t)}</td>
                            <td>
                              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: style.bg, color: style.color }}>
                                {style.icon} {style.label}
                              </span>
                            </td>
                            <td style={{ fontSize: 11, color: p.correction_flag ? '#EF4444' : '#94A3B8' }}>{p.correction_flag || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
                <div className="chartCard">
                  <h2>{t("admin.noConcordanceData")}</h2>
                  <p style={{ color: '#94A3B8', fontSize: 14, marginTop: 12 }}>
                    {t("admin.noConcordanceDesc")}
                  </p>
                </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
