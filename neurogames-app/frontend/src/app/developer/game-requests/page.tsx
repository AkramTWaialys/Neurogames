"use client";

import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  BookOpen,
  CheckCircle2,
  Clipboard,
  FileArchive,
  History,
  ListChecks,
  RefreshCw,
  Send,
  Terminal,
  Upload,
  Package,
  Code2,
  Zap,
} from "lucide-react";
import { AdminAPI, GameModuleRequest } from "@/lib/adminApi";
import "../../dashboard/admin.css";

const MAX_GAME_ZIP_BYTES = 5 * 1024 * 1024;
type RequestStatusFilter = "all" | GameModuleRequest["status"];

const REQUEST_STATUS_FILTERS: RequestStatusFilter[] = ["all", "pending", "accepted", "rejected"];
const PACKAGE_TREE = `my-game.zip
  index.html
  styles.css
  game.js
  assets/
    sprite.png
    success.mp3`;
const CORE_TELEMETRY_FIELDS = [
  "Participant_ID",
  "Game_Session_ID",
  "Age_Group",
  "Cognitive_Level",
  "Game_Completion_Status",
  "Performance_Level",
  "Time_Spent",
  "Total_Actions",
  "Correct_Responses",
  "Incorrect_Responses",
  "Hint_Usage",
  "Touch_Interactions",
  "Reaction_Time",
  "pause_count",
  "retry_count",
  "rt_cv",
  "level",
  "max_level_reached",
  "rounds_played",
  "rounds_passed",
  "session_outcome",
  "schema_version",
] as const;

function statusBadgeClass(status: GameModuleRequest["status"]): string {
  if (status === "accepted") return "ok";
  if (status === "rejected") return "fail";
  return "warn";
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

function statusLabel(status: RequestStatusFilter): string {
  if (status === "all") return "All";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function parseFeatureList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, arr) => arr.indexOf(item) === index);
}

function buildSamplePayload(gameId: string, features: string[]): Record<string, unknown> {
  const slug = gameId.trim().toLowerCase() || "my_game";
  const payload: Record<string, unknown> = {
    Participant_ID: "existing_neurogames_player",
    Game_Session_ID: `${slug}_session_001`,
    Age_Group: "9-11",
    Cognitive_Level: "Medium",
    Game_Completion_Status: "Completed",
    Performance_Level: "Medium",
    Time_Spent: 92.4,
    Total_Actions: 48,
    Correct_Responses: 31,
    Incorrect_Responses: 17,
    Hint_Usage: 2,
    Touch_Interactions: 48,
    Reaction_Time: 0.83,
    pause_count: 1,
    retry_count: 0,
    rt_cv: 0.18,
    level: 3,
    max_level_reached: 4,
    rounds_played: 6,
    rounds_passed: 4,
    session_outcome: "completed",
    schema_version: "1.0",
  };
  features.forEach((feature) => {
    payload[feature] = 0;
  });
  return payload;
}

function propertyTypeForField(field: string): "string" | "number" | "integer" {
  if (
    [
      "Participant_ID",
      "Game_Session_ID",
      "Age_Group",
      "Cognitive_Level",
      "Game_Completion_Status",
      "Performance_Level",
      "session_outcome",
      "schema_version",
    ].includes(field)
  ) {
    return "string";
  }
  if (
    [
      "Total_Actions",
      "Correct_Responses",
      "Incorrect_Responses",
      "Hint_Usage",
      "Touch_Interactions",
      "pause_count",
      "retry_count",
      "level",
      "max_level_reached",
      "rounds_played",
      "rounds_passed",
    ].includes(field)
  ) {
    return "integer";
  }
  return "number";
}

function buildTelemetrySchema(features: string[]): Record<string, unknown> {
  const fields = [...CORE_TELEMETRY_FIELDS, ...features];
  return {
    type: "object",
    required: fields,
    properties: Object.fromEntries(
      fields.map((field) => [field, { type: propertyTypeForField(field) }])
    ),
    additionalProperties: true,
  };
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function bridgeSnippet(gameId: string, features: string[]): string {
  return `const payload = ${formatJson(buildSamplePayload(gameId, features))};

window.parent.postMessage({
  type: "neurogames:session",
  payload
}, "*");`;
}

function parseRequiredJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label} is required`);
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${label} must be a JSON object`);
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label} must be valid JSON`);
  }
}

function buildCodeSummary(features: string[]): string {
  return [
    "Static HTML5 browser package.",
    "ZIP must contain index.html and static assets only.",
    "Game sends one end-of-session message with type neurogames:session through window.parent.postMessage.",
    features.length
      ? `Declared custom telemetry features: ${features.join(", ")}.`
      : "No custom telemetry features declared beyond the shared NeuroGames fields.",
  ].join("\n");
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(new Error("Could not read ZIP artifact"));
    reader.readAsDataURL(file);
  });
}

/* ─────────────────────────────────────────────
   TAB 1 — Integration Guide
───────────────────────────────────────────── */
function GuideTab({
  bridgeCode,
  copyGuideText,
  guideMessage,
}: {
  bridgeCode: string;
  copyGuideText: (text: string, message: string) => void;
  guideMessage: string;
}) {
  return (
    <div>
      {guideMessage && (
        <div className="developerAlert developerAlertInfo" style={{ marginBottom: 20 }}>
          {guideMessage}
        </div>
      )}

      {/* Steps KPI row */}
      <div className="kpiGrid" style={{ marginBottom: 24 }}>
        {[
          { icon: Package, num: "01", title: "Package static files", desc: "Bundle HTML, CSS, JS, images into a single ZIP with index.html at the root." },
          { icon: Code2, num: "02", title: "Connect to the platform", desc: "Call window.parent.postMessage with type \"neurogames:session\" when the session ends." },
          { icon: Zap, num: "03", title: "Submit for review", desc: "Attach the ZIP and a sample JSON payload matching what your game sends at session end." },
        ].map((step) => {
          const Icon = step.icon;
          return (
            <div className="kpiCard" key={step.num} style={{ flexDirection: "column", alignItems: "flex-start", gap: 10 }}>
              <span className="kpiIcon"><Icon size={18} /></span>
              <div className="kpiContent" style={{ width: "100%" }}>
                <h3>{step.num} — {step.title}</h3>
                <p style={{ fontSize: 13, color: "var(--admin-text-muted)", margin: "4px 0 0", lineHeight: 1.5 }}>{step.desc}</p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="chartCard" style={{ marginBottom: 16 }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <ListChecks size={15} /> Required payload fields
        </h2>
        <div className="devFieldChips">
          {CORE_TELEMETRY_FIELDS.map((field) => (
            <code key={field} className="devFieldChip">{field}</code>
          ))}
        </div>
      </div>

      {/* Reference cards */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 0.4fr) minmax(0, 1fr)", gap: 16 }}>
        <div className="chartCard" style={{ margin: 0 }}>
          <h2 style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <FileArchive size={15} /> ZIP Structure
          </h2>
          <pre className="devRefCode">{PACKAGE_TREE}</pre>
        </div>

        <div className="chartCard" style={{ margin: 0 }}>
          <h2 style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}><Terminal size={15} /> End-of-game bridge</span>
            <button
              className="pageBtn"
              type="button"
              style={{ display: "flex", alignItems: "center", gap: 6 }}
              onClick={() => copyGuideText(bridgeCode, "Code copied to clipboard.")}
            >
              <Clipboard size={13} /> Copy
            </button>
          </h2>
          <pre className="devRefCode devRefCode--bridge">{bridgeCode}</pre>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   TAB 2 — Game Submission
───────────────────────────────────────────── */
function SubmitTab({
  gameId, setGameId,
  displayName, setDisplayName,
  domain, setDomain,
  description, setDescription,
  features, setFeatures,
  artifactFile, setArtifactFile,
  samplePayload, setSamplePayload,
  featureList,
  loading, error, success, guideMessage,
  handleSubmit, insertSamplePayload, refresh,
}: {
  gameId: string; setGameId: (v: string) => void;
  displayName: string; setDisplayName: (v: string) => void;
  domain: string; setDomain: (v: string) => void;
  description: string; setDescription: (v: string) => void;
  features: string; setFeatures: (v: string) => void;
  artifactFile: File | null; setArtifactFile: (v: File | null) => void;
  samplePayload: string; setSamplePayload: (v: string) => void;
  featureList: string[];
  loading: boolean; error: string; success: string; guideMessage: string;
  handleSubmit: (e: FormEvent) => void;
  insertSamplePayload: () => void;
  refresh: () => void;
}) {
  const checklist = [
    { label: "Game ID & display name", complete: Boolean(gameId.trim() && displayName.trim()), detail: gameId.trim() ? `${displayName || "?"} / ${gameId}` : "Not set" },
    { label: "Cognitive target", complete: Boolean(domain.trim()), detail: domain.trim() || "Not set" },
    { label: "ZIP package", complete: Boolean(artifactFile), detail: artifactFile ? `${artifactFile.name} (${(artifactFile.size / 1024 / 1024).toFixed(2)} MB)` : "No file" },
    { label: "Sample payload", complete: Boolean(samplePayload.trim()), detail: samplePayload.trim() ? "Ready" : "Missing" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.3fr) minmax(280px, 0.5fr)", gap: 20, alignItems: "start" }}>
      {/* Left: form */}
      <div>
        {error && <div className="developerAlert developerAlertError">{error}</div>}
        {success && <div className="developerAlert developerAlertSuccess">{success}</div>}
        {guideMessage && <div className="developerAlert developerAlertInfo">{guideMessage}</div>}

        <form className="chartCard devSubmitFormCard" onSubmit={handleSubmit}>
          {/* Section 1 */}
          <div className="devFormSection">
            <div className="devFormSectionTitle">
              <span className="devFormStep">1</span>
              <h3>Game Identity</h3>
            </div>
            <div className="devThreeCol">
              <label className="devField">
                <span>Game ID</span>
                <input value={gameId} onChange={(e) => setGameId(e.target.value)} placeholder="auditory_attention" required />
                <small>Lowercase slug — do not reuse built-in IDs.</small>
              </label>
              <label className="devField">
                <span>Display name</span>
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Auditory Attention" required />
              </label>
              <label className="devField">
                <span>Cognitive target</span>
                <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="Attention, planning…" required />
              </label>
            </div>
            <label className="devField">
              <span>What does the player do?</span>
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Describe the core interaction, goal, and when a session ends." required />
            </label>
          </div>

          {/* Section 2 */}
          <div className="devFormSection">
            <div className="devFormSectionTitle">
              <span className="devFormStep">2</span>
              <h3>Package</h3>
            </div>
            <label className="devFileBox">
              <input type="file" accept=".zip,application/zip,application/x-zip-compressed" onChange={(e) => setArtifactFile(e.target.files?.[0] || null)} required />
              <span className="devFileIcon"><Upload size={18} /></span>
              <span>
                <strong>{artifactFile ? artifactFile.name : "Choose ZIP package"}</strong>
                <small>index.html at root · static assets only · 5 MB max</small>
              </span>
            </label>
          </div>

          {/* Section 3 */}
          <div className="devFormSection">
            <div className="devFormSectionTitle">
              <span className="devFormStep">3</span>
              <h3 style={{ flex: 1 }}>Telemetry</h3>
              <button className="pageBtn" type="button" onClick={insertSamplePayload} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle2 size={14} /> Insert sample
              </button>
            </div>
            <label className="devField">
              <span>Custom metrics</span>
              <textarea value={features} onChange={(e) => setFeatures(e.target.value)} placeholder={"target_speed\nplanning_score"} />
              <small>Optional · lower_snake_case · don&apos;t repeat shared fields.</small>
            </label>
            <label className="devField">
              <span>Sample session payload JSON</span>
              <textarea
                className="devPayloadTextarea"
                value={samplePayload}
                onChange={(e) => setSamplePayload(e.target.value)}
                placeholder="Paste or insert the JSON your game sends at session end."
                required
              />
              <small>Must exactly match what the game sends.</small>
            </label>
          </div>

          <div style={{ paddingTop: 8 }}>
            <button className="devSubmitBtn" type="submit" disabled={loading}>
              {artifactFile ? <Upload size={15} /> : <Send size={15} />}
              {loading ? "Submitting…" : "Submit for review"}
            </button>
          </div>
        </form>
      </div>

      {/* Right: readiness sidebar */}
      <div className="chartCard" style={{ position: "sticky", top: 24 }}>
        <h2 style={{ marginBottom: 14 }}>Package readiness</h2>
        <div style={{ display: "grid", gap: 10, marginBottom: 16 }}>
          {checklist.map((item) => (
            <div key={item.label} className={`devCheckItem ${item.complete ? "devCheckItem--done" : ""}`}>
              <CheckCircle2 size={15} />
              <div>
                <strong>{item.label}</strong>
                <p>{item.detail}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="devPreviewBox">
          <span>Preview</span>
          <dl>
            <div><dt>Game ID</dt><dd>{gameId.trim() || "—"}</dd></div>
            <div><dt>ZIP</dt><dd>{artifactFile?.name || "—"}</dd></div>
            <div><dt>Custom metrics</dt><dd>{featureList.length ? `${featureList.length} declared` : "Shared only"}</dd></div>
          </dl>
        </div>
        <div style={{ marginTop: 14 }}>
          <button className="pageBtn" type="button" onClick={refresh} disabled={loading} style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", justifyContent: "center" }}>
            <RefreshCw size={14} /> Refresh requests
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   TAB 3 — History
───────────────────────────────────────────── */
function HistoryTab({
  requests,
  statusFilter,
  setStatusFilter,
  refresh,
  loading,
}: {
  requests: GameModuleRequest[];
  statusFilter: RequestStatusFilter;
  setStatusFilter: (v: RequestStatusFilter) => void;
  refresh: () => void;
  loading: boolean;
}) {
  const counts = REQUEST_STATUS_FILTERS.reduce<Record<RequestStatusFilter, number>>(
    (acc, s) => { acc[s] = s === "all" ? requests.length : requests.filter((r) => r.status === s).length; return acc; },
    { all: 0, pending: 0, accepted: 0, rejected: 0 }
  );
  const visible = statusFilter === "all" ? requests : requests.filter((r) => r.status === statusFilter);

  return (
    <div>
      {/* Stats row */}
      <div className="kpiGrid" style={{ marginBottom: 20 }}>
        {REQUEST_STATUS_FILTERS.map((s) => (
          <div className="kpiCard" key={s}>
            <div className="kpiContent">
              <h3>{statusLabel(s)}</h3>
              <div className="kpiValue">{counts[s]}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Filter bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
        <span style={{ fontSize: 12, color: "var(--admin-text-muted)", fontWeight: 700 }}>Filter:</span>
        <div className="tabBar" style={{ marginBottom: 0, borderBottom: "none", gap: 4 }}>
          {REQUEST_STATUS_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              className={`tab ${statusFilter === s ? "tabActive" : ""}`}
              onClick={() => setStatusFilter(s)}
            >
              {statusLabel(s)} <span className="devBadge">{counts[s]}</span>
            </button>
          ))}
        </div>
        <button className="pageBtn" type="button" onClick={refresh} disabled={loading} style={{ marginInlineStart: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {/* Requests table */}
      <div className="chartCard" style={{ padding: 0, overflow: "hidden" }}>
        {visible.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--admin-text-muted)", fontSize: 13, fontWeight: 700 }}>
            No {statusFilter === "all" ? "" : `${statusFilter} `}requests found.
          </div>
        ) : (
          <table className="dataTable">
            <thead>
              <tr>
                <th>Game</th>
                <th>Status</th>
                <th>Submitted</th>
                <th>Decision</th>
                <th>Package</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((req) => (
                <tr key={req.id}>
                  <td>
                    <div style={{ fontWeight: 700, color: "var(--admin-text)" }}>{req.display_name}</div>
                    <div style={{ fontSize: 11, color: "var(--admin-text-muted)", marginTop: 2 }}>{req.game_id}</div>
                    {req.review_notes && (
                      <div style={{ fontSize: 11, color: "var(--admin-text-muted)", marginTop: 4, fontStyle: "italic" }}>{req.review_notes}</div>
                    )}
                  </td>
                  <td>
                    <span className={`developerStatusBadge ${statusBadgeClass(req.status)}`}>{req.status}</span>
                  </td>
                  <td style={{ fontSize: 12 }}>{formatDate(req.created_at) || "—"}</td>
                  <td style={{ fontSize: 12 }}>{req.decided_at ? formatDate(req.decided_at) : <span style={{ color: "var(--admin-text-muted)", fontStyle: "italic" }}>Pending</span>}</td>
                  <td style={{ fontSize: 12 }}>
                    {req.code_artifact_filename || <span style={{ color: "var(--admin-text-muted)" }}>—</span>}
                    {req.published_entry_url && (
                      <a href={req.published_entry_url} target="_blank" rel="noreferrer" style={{ display: "block", color: "var(--admin-primary)", fontSize: 11, marginTop: 2 }}>
                        View package →
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────
   Inner Developer Console Component
───────────────────────────────────────────── */
function DeveloperGameRequestsInner() {
  const searchParams = useSearchParams();
  const activeTab = searchParams.get("tab") || "guide";

  const [requests, setRequests] = useState<GameModuleRequest[]>([]);
  const [gameId, setGameId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [domain, setDomain] = useState("");
  const [description, setDescription] = useState("");
  const [features, setFeatures] = useState("");
  const [artifactFile, setArtifactFile] = useState<File | null>(null);
  const [samplePayload, setSamplePayload] = useState("");
  const [statusFilter, setStatusFilter] = useState<RequestStatusFilter>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [guideMessage, setGuideMessage] = useState("");

  const featureList = useMemo(() => parseFeatureList(features), [features]);
  const bridgeCode = useMemo(() => bridgeSnippet(gameId, featureList), [gameId, featureList]);

  async function refresh() {
    try {
      const data = await AdminAPI.getGameModuleRequests("all");
      setRequests(data?.requests || []);
    } catch { /* silent */ }
  }

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(async () => {
      try {
        const data = await AdminAPI.getGameModuleRequests("all");
        if (!cancelled) setRequests(data?.requests || []);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load requests");
      }
    });
    return () => { cancelled = true; };
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setSuccess("");
    try {
      if (!artifactFile) throw new Error("Static game package ZIP is required");
      if (!artifactFile.name.toLowerCase().endsWith(".zip")) throw new Error("Game package must be a .zip file");
      if (artifactFile.size > MAX_GAME_ZIP_BYTES) throw new Error("Game package ZIP must be 5 MB or smaller");
      const codeArtifactBase64 = await fileToBase64(artifactFile);
      const result = await AdminAPI.submitGameModuleRequest({
        game_id: gameId,
        display_name: displayName,
        cognitive_domain: domain,
        description,
        integration_mode: "manual",
        feature_set: featureList,
        code_summary: buildCodeSummary(featureList),
        code_artifact_filename: artifactFile?.name,
        code_artifact_base64: codeArtifactBase64,
        telemetry_schema_json: buildTelemetrySchema(featureList),
        sample_payload_json: parseRequiredJsonObject(samplePayload, "Sample payload"),
      });
      if (result?.request) {
        setSuccess("Request sent to superadmin for review.");
        setGuideMessage("");
        await refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  function insertSamplePayload() {
    setSamplePayload(formatJson(buildSamplePayload(gameId, featureList)));
    setGuideMessage("Sample payload inserted from the current game ID and feature list.");
  }

  async function copyGuideText(text: string, message: string) {
    try {
      await navigator.clipboard.writeText(text);
      setGuideMessage(message);
    } catch {
      setGuideMessage("Clipboard unavailable — select the snippet manually.");
    }
  }

  return (
    <div className="adminShell">
      <main className="adminMain">
        {/* Page header */}
        <header className="adminPageHeader">
          <h1>Developer Console</h1>
          <p>Prepare, submit, and track browser games for the NeuroGames platform.</p>
        </header>

        {/* Tab content */}
        {activeTab === "guide" && (
          <GuideTab
            bridgeCode={bridgeCode}
            copyGuideText={copyGuideText}
            guideMessage={guideMessage}
          />
        )}
        {activeTab === "submit" && (
          <SubmitTab
            gameId={gameId} setGameId={setGameId}
            displayName={displayName} setDisplayName={setDisplayName}
            domain={domain} setDomain={setDomain}
            description={description} setDescription={setDescription}
            features={features} setFeatures={setFeatures}
            artifactFile={artifactFile} setArtifactFile={setArtifactFile}
            samplePayload={samplePayload} setSamplePayload={setSamplePayload}
            featureList={featureList}
            loading={loading} error={error} success={success} guideMessage={guideMessage}
            handleSubmit={handleSubmit}
            insertSamplePayload={insertSamplePayload}
            refresh={refresh}
          />
        )}
        {activeTab === "history" && (
          <HistoryTab
            requests={requests}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            refresh={refresh}
            loading={loading}
          />
        )}
      </main>
    </div>
  );
}

export default function DeveloperGameRequestsPage() {
  return (
    <Suspense fallback={<div className="adminShell"><main className="adminMain">Loading Developer Console…</main></div>}>
      <DeveloperGameRequestsInner />
    </Suspense>
  );
}

