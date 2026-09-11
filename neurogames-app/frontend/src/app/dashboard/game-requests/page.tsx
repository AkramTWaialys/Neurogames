"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import AdminSidebar from "@/components/admin/AdminSidebar";
import { AdminAPI, GameModuleRequest, GameModuleScanReport } from "@/lib/adminApi";
import { isSuperAdmin } from "@/lib/adminAuth";
import { useRouter } from "next/navigation";
import "../admin.css";

function formatBytes(value?: number | null): string {
  if (!value) return "not attached";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function GameRequestsReviewPage() {
  const router = useRouter();
  const [requests, setRequests] = useState<GameModuleRequest[]>([]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [acceptedStatus, setAcceptedStatus] = useState<Record<number, "draft" | "active">>({});
  const [scanReports, setScanReports] = useState<Record<number, GameModuleScanReport>>({});
  const [expandedRequestId, setExpandedRequestId] = useState<number | null>(null);
  const [scanningId, setScanningId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    const data = await AdminAPI.getGameModuleRequests();
    setRequests(data?.requests || []);
  }

  useEffect(() => {
    if (!isSuperAdmin()) {
      router.replace("/dashboard/overview");
      return;
    }
    queueMicrotask(async () => {
      try {
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load requests");
      }
    });
  }, [router]);

  async function decide(requestId: number, decision: "accepted" | "rejected") {
    setLoading(true);
    setError("");
    try {
      await AdminAPI.decideGameModuleRequest(requestId, {
        decision,
        review_notes: notes[requestId] || undefined,
        accepted_status: acceptedStatus[requestId] || "active",
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
    } finally {
      setLoading(false);
    }
  }

  async function scanRequest(requestId: number) {
    setScanningId(requestId);
    setError("");
    try {
      const result = await AdminAPI.scanGameModuleRequest(requestId);
      if (result?.scan) {
        setScanReports((prev) => ({ ...prev, [requestId]: result.scan }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanningId(null);
    }
  }

  function scanBadgeClass(status: GameModuleScanReport["overall_status"] | "PASS" | "WARNING" | "FAIL") {
    if (status === "PASS") return "ok";
    if (status === "WARNING") return "warn";
    return "fail";
  }

  return (
    <div className="adminShell">
      <AdminSidebar />
      <main className="adminMain">
        <header className="adminPageHeader">
          <h1>Developer Game Requests</h1>
          <p>Review developer submissions before adding compatible ADHD game modules to NeuroGames.</p>
        </header>

        {error && <div className="moduleAlert">{error}</div>}

        <div className="moduleActions" style={{ marginBottom: 20 }}>
          <button className="reportExportBtn" type="button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} /> Refresh
          </button>
        </div>

        <div className="moduleLayout" style={{ gridTemplateColumns: "1fr" }}>
          {requests.map((request) => {
            const expanded = expandedRequestId === request.id;
            const scanReport = scanReports[request.id];
            return (
            <section className="chartCard modulePanel" key={request.id}>
              <button
                className="requestSummaryButton"
                type="button"
                onClick={() => setExpandedRequestId((current) => (current === request.id ? null : request.id))}
                aria-expanded={expanded}
              >
                <span className="requestSummaryChevron">
                  {expanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                </span>
                <span className="requestSummaryMain">
                  <span className="requestSummaryTitle">{request.display_name}</span>
                  <span className="requestSummaryMeta">
                    {request.game_id} | {request.cognitive_domain} | {request.integration_mode}
                  </span>
                </span>
                <span className="requestSummaryDetails">
                  <span>{request.developer_username}</span>
                  <span>{request.code_artifact_filename || "no ZIP"}</span>
                  <span>{new Date(request.created_at).toLocaleDateString()}</span>
                </span>
                {scanReport && (
                  <span className={`moduleBadge ${scanBadgeClass(scanReport.overall_status)}`}>
                    Scan: {scanReport.overall_status}
                  </span>
                )}
                <span className={`moduleBadge ${request.status === "pending" ? "warn" : request.status === "accepted" ? "ok" : "muted"}`}>
                  {request.status}
                </span>
              </button>

              {expanded && (
                <>
              <div className="moduleMetaGrid">
                <div>
                  <span>Developer</span>
                  <strong>{request.developer_username}</strong>
                </div>
                <div>
                  <span>Email</span>
                  <strong>{request.developer_email || "not provided"}</strong>
                </div>
                <div>
                  <span>Phone</span>
                  <strong>{request.developer_phone || "not provided"}</strong>
                </div>
                <div>
                  <span>Created</span>
                  <strong>{new Date(request.created_at).toLocaleString()}</strong>
                </div>
              </div>

              {request.description && <p className="moduleNote">{request.description}</p>}

              <h2>Declared Features</h2>
              <div className="moduleContractList">
                {(request.feature_set || []).map((feature) => (
                  <code key={feature}>{feature}</code>
                ))}
                {(request.feature_set || []).length === 0 && <span className="moduleNote">No custom features declared.</span>}
              </div>

              <h2>Code And Telemetry</h2>
              <div className="moduleMetaGrid">
                <div>
                  <span>Repository/code link</span>
                  <strong>{request.code_repository_url || "not provided"}</strong>
                </div>
                <div>
                  <span>Attached ZIP package</span>
                  <strong>
                    {request.code_artifact_filename
                      ? `${request.code_artifact_filename} (${formatBytes(request.code_artifact_size)})`
                      : "not attached"}
                  </strong>
                </div>
                <div>
                  <span>Published entry</span>
                  <strong>{request.published_entry_url || "not published yet"}</strong>
                </div>
                <div>
                  <span>Created game id</span>
                  <strong>{request.created_game_id || "not yet created"}</strong>
                </div>
              </div>
              {request.code_artifact_filename && request.status === "pending" && (
                <p className="moduleNote">
                  Accepting this request will publish the ZIP as a sandboxed static game package if it contains a valid index.html.
                </p>
              )}
              {request.code_summary && <pre className="moduleCode">{request.code_summary}</pre>}
              {request.telemetry_schema_json !== undefined && request.telemetry_schema_json !== null && (
                <pre className="moduleCode">{JSON.stringify(request.telemetry_schema_json, null, 2)}</pre>
              )}
              {request.sample_payload_json !== undefined && request.sample_payload_json !== null && (
                <pre className="moduleCode">{JSON.stringify(request.sample_payload_json, null, 2)}</pre>
              )}

              {request.status === "pending" ? (
                <div className="moduleSettingsForm">
                  <div className="moduleActions">
                    <button
                      className="reportExportBtn"
                      type="button"
                      onClick={() => scanRequest(request.id)}
                      disabled={scanningId === request.id || loading}
                    >
                      <ShieldCheck size={16} /> {scanningId === request.id ? "Scanning..." : "Scan package"}
                    </button>
                    {scanReports[request.id] && (
                      <span className={`moduleBadge ${scanBadgeClass(scanReports[request.id].overall_status)}`}>
                        Scan: {scanReports[request.id].overall_status}
                      </span>
                    )}
                  </div>

                  {scanReports[request.id] && (
                    <div className="scanReport">
                      <div className="moduleMetaGrid">
                        <div>
                          <span>Recommendation</span>
                          <strong>{scanReports[request.id].recommendation}</strong>
                        </div>
                        <div>
                          <span>Feature count</span>
                          <strong>{scanReports[request.id].features.feature_count}</strong>
                        </div>
                        <div>
                          <span>Missing core fields</span>
                          <strong>
                            {scanReports[request.id].telemetry.missing_core_fields.length
                              ? scanReports[request.id].telemetry.missing_core_fields.join(", ")
                              : "none"}
                          </strong>
                        </div>
                        <div>
                          <span>Missing declared features</span>
                          <strong>
                            {scanReports[request.id].telemetry.missing_declared_features.length
                              ? scanReports[request.id].telemetry.missing_declared_features.join(", ")
                              : "none"}
                          </strong>
                        </div>
                      </div>
                      <div className="scanCheckList">
                        {scanReports[request.id].checks.map((check, index) => (
                          <div className="scanCheck" key={`${check.category}-${check.name}-${index}`}>
                            <span className={`moduleBadge ${scanBadgeClass(check.status)}`}>{check.status}</span>
                            <div>
                              <strong>{check.category}: {check.name}</strong>
                              <p>{check.message}</p>
                              {check.details !== undefined && check.details !== null && (
                                <code>{JSON.stringify(check.details)}</code>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <label>
                    Review notes
                    <textarea
                      value={notes[request.id] || ""}
                      onChange={(event) => setNotes((prev) => ({ ...prev, [request.id]: event.target.value }))}
                    />
                  </label>
                  <label>
                    Accepted module status
                    <select
                      value={acceptedStatus[request.id] || "active"}
                      onChange={(event) =>
                        setAcceptedStatus((prev) => ({
                          ...prev,
                          [request.id]: event.target.value as "draft" | "active",
                        }))
                      }
                    >
                      <option value="active">active - visible as an available game module</option>
                      <option value="draft">draft - registered but not active in game list</option>
                    </select>
                  </label>
                  <div className="moduleActions">
                    <button
                      className="reportGenerateBtn"
                      type="button"
                      onClick={() => decide(request.id, "accepted")}
                      disabled={loading || scanReports[request.id]?.overall_status === "FAIL"}
                    >
                      <CheckCircle2 size={16} /> Accept and publish
                    </button>
                    <button className="reportExportBtn" type="button" onClick={() => decide(request.id, "rejected")} disabled={loading}>
                      <XCircle size={16} /> Reject
                    </button>
                  </div>
                </div>
              ) : (
                <p className="moduleNote">
                  Decision: {request.status}. {request.review_notes || ""}
                </p>
              )}
                </>
              )}
            </section>
            );
          })}
          {requests.length === 0 && (
            <section className="chartCard modulePanel">
              <h2>No developer requests yet</h2>
              <p className="moduleNote">Developer submissions will appear here for superadmin review.</p>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
