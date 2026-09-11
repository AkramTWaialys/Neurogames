/**
 * Admin API — fetch helpers for the admin dashboard.
 */

import { CONFIG } from "./config";
const API_BASE = CONFIG.API_BASE_URL;
import { authHeaders, logout } from "./authApi";
import { clearAdminSession } from "./adminAuth";

export interface AdminAlert {
  severity: "critical" | "warning" | "info";
  title: string;
  detail: string;
}

export interface AtRiskParticipant {
  participant_id: string;
  anomaly_rate: string | null;
  trend: string | null;
  last_session: string | null;
  reason: string;
}

export interface AdminStats {
  total_sessions: number;
  total_participants: number;
  avg_accuracy: number;
  avg_reaction_time: number;
  per_game: {
    game_id: string;
    game_name: string;
    color: string;
    accuracy: number;
    sessions: number;
  }[];
  last_updated: string | null;
  alerts: AdminAlert[];
  at_risk_participants: AtRiskParticipant[];
}

export interface PopulationData {
  profiles: { profile: string; count: number }[];
  daily_sessions: { date: string; sessions: number }[];
}

export interface ParticipantRow {
  participant_id: string;
  session_count: number;
  last_active: string;
}

export interface ParticipantsPage {
  participants: ParticipantRow[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface TimelinePoint {
  session_number: number;
  game_id: string;
  game_name: string;
  accuracy: number;
  reaction_time: number | null;
  level: number;
  duration_s: number;
  stored_at: string;
}

export interface GameAnalytics {
  game_id: string;
  game_name: string;
  total_sessions: number;
  accuracies: number[];
  reaction_times: number[];
  profile_accuracies: Record<string, number[]>;
}

export interface AnomalyProfileRate {
  profile: string;
  total: number;
  flagged: number;
  rate: number;
}

export interface AnomalyFlaggedParticipant {
  participant_id: string;
  flagged_sessions: number;
  total_sessions: number;
  rate: number;
}

export interface AnomalyData {
  game_id: string;
  game_name: string;
  total_sessions: number;
  combined_flagged: number;
  if_flagged: number;
  ae_flagged: number;
  flag_rate: number;
  profile_rates: AnomalyProfileRate[];
  if_scores: number[];
  ae_errors: number[];
  top_flagged: AnomalyFlaggedParticipant[];
}

export interface ClassificationPerClass {
  class: string;
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

export interface ClassificationMisclassified {
  participant_id: string;
  actual: string;
  predicted: string;
  confidence: number;
}

export interface ClassificationData {
  game_id: string;
  game_name: string;
  total: number;
  accuracy: number;
  avg_confidence: number;
  classes: string[];
  confusion_matrix: Record<string, Record<string, number>>;
  per_class: ClassificationPerClass[];
  misclassified: ClassificationMisclassified[];
}

export interface ConcordanceRecord {
  participant_id: string;
  conners_score: number;
  conners_tscore: number;
  conners_tier: string;
  ml_prediction: string;
  agreement_ratio: number;
  concordance: string;
  original_confidence: number;
  adjusted_confidence: number;
  correction_flag: string | null;
  computed_at: string;
}

export interface ConcordanceData {
  total_matched: number;
  n_concordant: number;
  n_partial: number;
  n_discordant: number;
  concordance_rate: number;
  exact_match_rate: number;
  avg_adjusted_confidence: number;
  participants: ConcordanceRecord[];
}

export interface AdminPlayerProfile {
  id: number;
  username: string;
  role: string;
  school_id: number | null;
  created_at: string | null;
  display_name?: string | null;
  age?: number | null;
  custom_school_name?: string | null;
  school_name?: string | null;
}

export interface GameModuleIntegrationStatus {
  decision: string;
  missing: string[];
  ml_enabled: boolean;
  included_in_cross_game: boolean;
  status: string;
}

export interface GameModule {
  game_id: string;
  display_name: string;
  cognitive_domain: string;
  description?: string | null;
  integration_mode: "builtin" | "manual" | "external_telemetry";
  feature_set: string[];
  label_schema: Record<string, unknown>;
  schema_version: string;
  ml_enabled: boolean;
  included_in_cross_game: boolean;
  status: "draft" | "active" | "paused" | "archived";
  is_builtin?: boolean;
  is_active?: boolean;
  session_count?: number;
  ml_eligible_sessions?: number;
  ingestion_keys?: GameIngestionKey[];
  integration_status?: GameModuleIntegrationStatus;
}

export interface GameIngestionKey {
  api_key?: string;
  key_prefix: string;
  created_at: string;
  label: string;
  revoked_at?: string | null;
}

export interface GameModuleRegistrationPayload {
  game_id: string;
  display_name: string;
  cognitive_domain: string;
  description?: string | null;
  integration_mode: "manual" | "external_telemetry";
  feature_set: string[];
  schema_version: string;
  ml_enabled: boolean;
  included_in_cross_game: boolean;
  status: "draft" | "active" | "paused" | "archived";
}

export type GameModuleUpdatePayload = Partial<Omit<GameModuleRegistrationPayload, "game_id">>;

export interface GameModuleContract {
  target_domain: "ADHD";
  label_schema: { type: string; labels: string[] };
  core_fields: string[];
  rules: string[];
  sample_payload: Record<string, unknown>;
}

export interface GameModuleRequestPayload {
  game_id: string;
  display_name: string;
  cognitive_domain: string;
  description?: string | null;
  integration_mode: "manual" | "external_telemetry";
  feature_set: string[];
  code_repository_url?: string | null;
  code_summary?: string | null;
  code_artifact_filename?: string | null;
  code_artifact_base64?: string | null;
  telemetry_schema_json?: unknown;
  sample_payload_json?: unknown;
}

export interface GameModuleRequest {
  id: number;
  developer_user_id: number;
  developer_username: string;
  developer_email?: string | null;
  developer_phone?: string | null;
  reviewer_username?: string | null;
  game_id: string;
  display_name: string;
  cognitive_domain: string;
  description?: string | null;
  integration_mode: "manual" | "external_telemetry";
  feature_set: string[];
  code_repository_url?: string | null;
  code_summary?: string | null;
  code_artifact_filename?: string | null;
  code_artifact_size?: number | null;
  published_entry_url?: string | null;
  telemetry_schema_json?: unknown;
  sample_payload_json?: unknown;
  status: "pending" | "accepted" | "rejected";
  review_notes?: string | null;
  created_game_id?: string | null;
  created_at: string;
  updated_at: string;
  decided_at?: string | null;
  game_module?: GameModule | null;
  ingestion_key?: GameIngestionKey | null;
}

export interface GameModuleScanCheck {
  category: string;
  name: string;
  status: "PASS" | "WARNING" | "FAIL";
  message: string;
  details?: unknown;
}

export interface GameModuleScanReport {
  request_id: number;
  game_id: string;
  display_name: string;
  overall_status: "PASS" | "WARNING" | "FAIL";
  recommendation: string;
  checks: GameModuleScanCheck[];
  features: {
    declared_features: string[];
    feature_count: number;
    label_schema: string;
  };
  telemetry: {
    has_sample_payload: boolean;
    missing_core_fields: string[];
    missing_declared_features: string[];
    extra_fields: string[];
  };
  package: {
    filename?: string | null;
    size_bytes?: number | null;
    entry_point?: string | null;
    files: string[];
  };
}

function handleUnauthorized(): void {
  logout();
  clearAdminSession();
  if (typeof window !== "undefined" && window.location.pathname !== "/") {
    window.location.assign("/");
  }
}

async function fetchJSON<T>(url: string): Promise<T | null> {
  try {
    const resp = await fetch(url, {
      headers: authHeaders(),
      signal: AbortSignal.timeout(30000)
    });
    if (!resp.ok) {
      if (resp.status === 401) {
        handleUnauthorized();
        return null;
      }
      if (resp.status === 404) return null;
      console.warn(`Admin API request failed: HTTP ${resp.status}`, url);
      return null;
    }
    return (await resp.json()) as T;
  } catch (err) {
    console.warn("Admin API request failed", url, err);
    return null;
  }
}

async function sendJSON<T>(url: string, method: string, body?: unknown): Promise<T | null> {
  try {
    const resp = await fetch(url, {
      method,
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000)
    });
    if (resp.status === 401) {
      handleUnauthorized();
      return null;
    }
    if (resp.status === 404) return null;
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}${text ? `: ${text}` : ""}`);
    }
    return (await resp.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "TimeoutError") {
      throw new Error("Request timed out. Check that the backend is running and try again.");
    }
    throw err;
  }
}

export const AdminAPI = {
  getStats: () => fetchJSON<AdminStats>(`${API_BASE}/api/admin/stats`),

  getPopulation: () => fetchJSON<PopulationData>(`${API_BASE}/api/admin/population`),

  getParticipants: (page = 1, pageSize = 20, search = "") =>
    fetchJSON<ParticipantsPage>(
      `${API_BASE}/api/dashboard/participants?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}`
    ),

  getTimeline: (pid: string) =>
    fetchJSON<{ participant_id: string; timeline: TimelinePoint[] }>(
      `${API_BASE}/api/dashboard/participants/${encodeURIComponent(pid)}/timeline`
    ),

  getGameAnalytics: (gameId: string) =>
    fetchJSON<GameAnalytics>(`${API_BASE}/api/admin/analytics/game/${gameId}`),

  getAnomalies: (gameId: string) =>
    fetchJSON<AnomalyData>(`${API_BASE}/api/admin/anomalies?game=${gameId}`),

  getClassification: (gameId: string) =>
    fetchJSON<ClassificationData>(`${API_BASE}/api/admin/classification?game=${gameId}`),

  getConcordance: () =>
    fetchJSON<ConcordanceData>(`${API_BASE}/api/admin/concordance`),

  getParticipantConcordance: (pid: string) =>
    fetchJSON<ConcordanceRecord>(
      `${API_BASE}/api/dashboard/participants/${encodeURIComponent(pid)}/concordance`
    ),

  downloadFhirBundle: (pid: string) =>
    fetch(`${API_BASE}/api/fhir/participants/${encodeURIComponent(pid)}/bundle`, {
      headers: authHeaders(),
    }),

  // Management
  deleteSchool: (id: number, headers?: Record<string, string>) =>
    fetch(`${API_BASE}/api/auth/schools/${id}`, { method: "DELETE", headers: headers || authHeaders() }),

  deleteAdmin: (id: number, headers?: Record<string, string>) =>
    fetch(`${API_BASE}/api/auth/admins/${id}`, { method: "DELETE", headers: headers || authHeaders() }),

  updateAdmin: (id: number, data: { school_id: number }, headers?: Record<string, string>) =>
    fetch(`${API_BASE}/api/auth/admins/${id}`, {
      method: "PATCH",
      headers: { ...(headers || authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  getPlayers: (headers?: Record<string, string>, schoolId?: number) => {
    const url = schoolId ? `${API_BASE}/api/auth/players?school_id=${schoolId}` : `${API_BASE}/api/auth/players`;
    return fetch(url, { headers: headers || authHeaders() });
  },

  getPlayerProfiles: (schoolId?: number) => {
    const url = schoolId ? `${API_BASE}/api/auth/players?school_id=${schoolId}` : `${API_BASE}/api/auth/players`;
    return fetchJSON<{ players: AdminPlayerProfile[] }>(url);
  },

  getGameModules: () =>
    fetchJSON<{ game_modules: GameModule[]; count: number }>(`${API_BASE}/api/game-modules`),

  createGameModule: (payload: GameModuleRegistrationPayload) =>
    sendJSON<{
      status: string;
      game_module: GameModule;
      ingestion_key: GameIngestionKey;
      integration_status: GameModuleIntegrationStatus;
      schema_url: string;
    }>(`${API_BASE}/api/game-modules`, "POST", payload),

  updateGameModule: (gameId: string, payload: GameModuleUpdatePayload) =>
    sendJSON<{
      status: string;
      game_module: GameModule;
      integration_status: GameModuleIntegrationStatus;
    }>(`${API_BASE}/api/game-modules/${encodeURIComponent(gameId)}`, "PATCH", payload),

  getGameModuleContract: () =>
    fetchJSON<GameModuleContract>(`${API_BASE}/api/game-modules/adhd-contract`),

  getGameModuleSchema: (gameId: string) =>
    fetchJSON<{
      game_module: GameModule;
      core_fields: string[];
      feature_set: string[];
      sample_payload: Record<string, unknown>;
    }>(`${API_BASE}/api/game-modules/${encodeURIComponent(gameId)}/schema`),

  rotateGameModuleKey: (gameId: string) =>
    sendJSON<{ status: string; revoked: number; ingestion_key: GameIngestionKey }>(
      `${API_BASE}/api/game-modules/${encodeURIComponent(gameId)}/rotate-key`,
      "POST"
    ),

  getGameModuleRequests: (status?: string) =>
    fetchJSON<{ requests: GameModuleRequest[] }>(
      `${API_BASE}/api/game-modules/requests${status ? `?status=${encodeURIComponent(status)}` : ""}`
    ),

  submitGameModuleRequest: (payload: GameModuleRequestPayload) =>
    sendJSON<{ status: string; request: GameModuleRequest }>(
      `${API_BASE}/api/game-modules/requests`,
      "POST",
      payload
    ),

  decideGameModuleRequest: (
    requestId: number,
    payload: { decision: "accepted" | "rejected"; review_notes?: string; accepted_status?: "draft" | "active" },
  ) =>
    sendJSON<{ status: string; request: GameModuleRequest }>(
      `${API_BASE}/api/game-modules/requests/${requestId}/decision`,
      "PATCH",
      payload
    ),

  scanGameModuleRequest: (requestId: number) =>
    sendJSON<{ status: string; scan: GameModuleScanReport }>(
      `${API_BASE}/api/game-modules/requests/${requestId}/scan`,
      "POST"
    ),
};
