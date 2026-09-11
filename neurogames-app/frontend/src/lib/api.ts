/**
 * NeuroAPI — Backend communication layer
 * Single server: the MLOps FastAPI backend on port 8000.
 * Start with: uvicorn mlops.server:app --host 0.0.0.0 --port 8000 --reload
 */

import { authHeaders } from "./authApi";
import { CONFIG } from "./config";
import { getResponseErrorMessage } from "./apiErrors";

const API_BASE = CONFIG.API_BASE_URL;

export interface PlayerLookup {
  age: number;
  age_group: string;
  cognitive_level: string;
}

export interface GameStat {
  game_id: string;
  game_name: string;
  sessions_played: number;
  avg_accuracy: number;
  best_score: number | null;
  max_level?: number;
  last_played: string | null;
}

export interface GameInfo {
  id: string;
  name: string;
  icon: string;
  cognitive_domain: string;
  description: string;
  difficulty_system: string;
  color: string;
  runtime_type?: string | null;
  entry_url?: string | null;
}

export interface ParticipantSummary {
  participant_id: string;
  total_sessions: number;
  avg_accuracy: number;
  games: GameStat[];
  xp: number;
  level: string;
}

export interface SessionDetailDTO {
  id: number;
  game_id: string;
  game_name: string;
  icon: string;
  correct: number;
  total_actions: number;
  accuracy: number;           // 0-100 scale from backend
  level: number;
  duration_s: number;
  stars: string;
  stored_at: string;          // ISO timestamp
  reaction_time: number | null;
  performance_level: string | null;
}

export interface SessionMetrics {
  duration_s: number;
  totalActions: number;
  correct: number;
  incorrect: number;
  avgRT_s?: number;
  completed: boolean;
  hintUsage?: number;
  roundsPlayed?: number;
  roundsPassed?: number;
  sessionOutcome?: string;
  roundDetails?: Record<string, unknown>[];
  level?: number;
}

export const NeuroAPI = {
  /** Fetch active games from the backend registry, including approved custom modules. */
  async getGames(): Promise<{ ok: boolean; games: GameInfo[] }> {
    try {
      const resp = await fetch(`${API_BASE}/api/games`, {
        headers: authHeaders(),
        signal: AbortSignal.timeout(5000),
      });
      if (!resp.ok) return { ok: false, games: [] };
      const data = (await resp.json()) as { games?: GameInfo[] };
      return { ok: true, games: data.games || [] };
    } catch {
      return { ok: false, games: [] };
    }
  },

  /** Upload a completed game session to the MLOps ingestion server. */
  async uploadSession(
    gameName: string,
    payload: Record<string, unknown>,
  ): Promise<{ ok: boolean; message: string }> {
    try {
      const resp = await fetch(`${API_BASE}/upload/${gameName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        return { ok: false, message: await getResponseErrorMessage(resp) };
      }
      const data = (await resp.json()) as { session_id?: string };
      return { ok: true, message: `Session ${data.session_id} sauvegardée ✓` };
    } catch {
      return { ok: false, message: "Serveur non disponible (mode hors-ligne)" };
    }
  },

  /** Health-check the FastAPI server. */
  async ping(): Promise<boolean> {
    try {
      const resp = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
      return resp.ok;
    } catch {
      return false;
    }
  },

  /** Fetch aggregated stats for a participant from the backend. */
  async getSummary(
    participantId: string,
  ): Promise<{ ok: boolean; data: ParticipantSummary | null }> {
    try {
      const resp = await fetch(
        `${API_BASE}/api/participants/${encodeURIComponent(participantId)}/summary`,
        { headers: authHeaders(), signal: AbortSignal.timeout(5000) },
      );
      if (!resp.ok) return { ok: false, data: null };
      const data = (await resp.json()) as ParticipantSummary;
      return { ok: true, data };
    } catch {
      return { ok: false, data: null };
    }
  },

  /** Fetch every individual session for a participant. */
  async getSessionHistory(
    participantId: string,
  ): Promise<{ ok: boolean; sessions: SessionDetailDTO[] }> {
    try {
      const resp = await fetch(
        `${API_BASE}/api/participants/${encodeURIComponent(participantId)}/sessions`,
        { headers: authHeaders(), signal: AbortSignal.timeout(5000) },
      );
      if (!resp.ok) return { ok: false, sessions: [] };
      const data = (await resp.json()) as { sessions: SessionDetailDTO[] };
      return { ok: true, sessions: data.sessions || [] };
    } catch {
      return { ok: false, sessions: [] };
    }
  },

  /** Look up a participant's info from fake data. */
  async lookupPlayer(
    participantId: string,
  ): Promise<{ ok: boolean; data: PlayerLookup | null }> {
    try {
      const resp = await fetch(`${API_BASE}/lookup/${encodeURIComponent(participantId)}`, {
        headers: authHeaders(),
        signal: AbortSignal.timeout(3000),
      });
      if (!resp.ok) return { ok: false, data: null };
      const data = (await resp.json()) as PlayerLookup;
      return { ok: true, data };
    } catch {
      return { ok: false, data: null };
    }
  },

  /** Build the base session payload matching BaseSessionSchema. */
  buildBase(
    player: { id: string; age?: number; ageGroup: string; cognitiveLevel: string },
    metrics: SessionMetrics,
  ): Record<string, unknown> {
    const acc = metrics.totalActions > 0 ? metrics.correct / metrics.totalActions : 0;
    const perfLevel = acc >= 0.75 ? "Optimal" : acc >= 0.45 ? "Struggling" : "Disengaged";
    const payload: Record<string, unknown> = {
      Participant_ID: player.id,
      Game_Session_ID: `${player.id}_${Date.now()}`,
      Age: player.age || 9,
      Age_Group: player.ageGroup,
      Cognitive_Level: (() => {
        const lo = player.cognitiveLevel.toLowerCase();
        if (lo === "high" || lo === "advanced") return "Advanced";
        if (lo === "low" || lo === "early") return "Early";
        return "Developing";
      })(),
      Game_Completion_Status: metrics.completed ? "Completed" : "Not Completed",
      Performance_Level: perfLevel,
      Time_Spent: Math.round(metrics.duration_s * 10) / 10,
      Total_Actions: metrics.totalActions,
      Correct_Responses: metrics.correct,
      Incorrect_Responses: metrics.incorrect,
      Touch_Interactions: metrics.totalActions,
      Reaction_Time: Math.round((metrics.avgRT_s || 0) * 1000) / 1000,
      level: metrics.level || 1,
      max_level_reached: metrics.level || 1,
      cluster: "Unknown",
      played_at: new Date().toISOString(),
      rounds_played: metrics.roundsPlayed || 1,
      rounds_passed: metrics.roundsPassed || 0,
      session_outcome: metrics.sessionOutcome || (metrics.completed ? "completed" : "failed_at_round_1"),
      round_details: JSON.stringify(metrics.roundDetails || []),
    };
    if (typeof metrics.hintUsage === "number") {
      payload.Hint_Usage = metrics.hintUsage;
    }
    return payload;
  },

  /** Fetch the latest pre-generated report (no LLM call, instant). */
  async getLatestReport(
    participantId: string,
    locale?: string,
  ): Promise<{
    ok: boolean;
    reportText: string;
    method: string;
    structured: Record<string, unknown> | null;
    reportDate: string;
    error?: string;
  }> {
    try {
      const localeParam = locale ? `?locale=${encodeURIComponent(locale)}` : "";
      const resp = await fetch(
        `${API_BASE}/report/${encodeURIComponent(participantId)}/latest${localeParam}`,
        { headers: authHeaders(), signal: AbortSignal.timeout(5000) },
      );
      if (!resp.ok) {
        if (resp.status === 404) {
          return { ok: false, reportText: "", method: "none", structured: null, reportDate: "", error: "no_report" };
        }
        return {
          ok: false, reportText: "", method: "error",
          structured: null, reportDate: "",
          error: await getResponseErrorMessage(resp),
        };
      }
      const data = (await resp.json()) as {
        report_text?: string;
        method?: string;
        structured?: Record<string, unknown>;
        report_date?: string;
      };
      return {
        ok: true,
        reportText: data.report_text || "",
        method: data.method || "pre_generated",
        structured: data.structured || null,
        reportDate: data.report_date || "",
      };
    } catch {
      return {
        ok: false, reportText: "", method: "error",
        structured: null, reportDate: "",
        error: "Serveur non disponible",
      };
    }
  },

  /** Check report readiness status for a participant. */
  async getReportStatus(
    participantId: string,
  ): Promise<{
    ok: boolean;
    totalSessions: number;
    hasReport: boolean;
    reportDate: string | null;
    sessionsSinceReport: number;
    eligible: boolean;
    reason: string | null;
  }> {
    try {
      const resp = await fetch(
        `${API_BASE}/report/${encodeURIComponent(participantId)}/status`,
        { headers: authHeaders(), signal: AbortSignal.timeout(3000) },
      );
      if (!resp.ok) return { ok: false, totalSessions: 0, hasReport: false, reportDate: null, sessionsSinceReport: 0, eligible: false, reason: null };
      const data = (await resp.json()) as {
        total_sessions?: number;
        has_report?: boolean;
        report_date?: string | null;
        sessions_since_report?: number;
        eligible?: boolean;
        reason?: string | null;
      };
      return {
        ok: true,
        totalSessions: data.total_sessions || 0,
        hasReport: data.has_report || false,
        reportDate: data.report_date || null,
        sessionsSinceReport: data.sessions_since_report || 0,
        eligible: data.eligible || false,
        reason: data.reason || null,
      };
    } catch {
      return { ok: false, totalSessions: 0, hasReport: false, reportDate: null, sessionsSinceReport: 0, eligible: false, reason: null };
    }
  },

  /** Request on-demand report generation (queued, returns task_id). */
  async requestReport(
    participantId: string,
    locale?: string,
  ): Promise<{
    ok: boolean;
    status: string;
    taskId: string | null;
    pollUrl: string | null;
    error?: string;
  }> {
    try {
      const localeParam = locale ? `?locale=${encodeURIComponent(locale)}` : "";
      const resp = await fetch(
        `${API_BASE}/report/${encodeURIComponent(participantId)}/request${localeParam}`,
        { method: "POST", headers: authHeaders(), signal: AbortSignal.timeout(5000) },
      );
      if (!resp.ok) {
        return {
          ok: false, status: "error", taskId: null, pollUrl: null,
          error: await getResponseErrorMessage(resp),
        };
      }
      const data = (await resp.json()) as {
        status?: string;
        task_id?: string;
        poll_url?: string;
      };
      return {
        ok: true,
        status: data.status || "accepted",
        taskId: data.task_id || null,
        pollUrl: data.poll_url || null,
      };
    } catch {
      return { ok: false, status: "error", taskId: null, pollUrl: null, error: "Serveur non disponible" };
    }
  },

  /** Check report generation queue status. */
  async getQueueStatus(
    participantId: string,
  ): Promise<{
    ok: boolean;
    generating: boolean;
    taskId: string | null;
    position: number;
    state: string | null;
    elapsedSeconds: number | null;
  }> {
    try {
      const resp = await fetch(
        `${API_BASE}/report/${encodeURIComponent(participantId)}/queue-status`,
        { headers: authHeaders(), signal: AbortSignal.timeout(3000) },
      );
      if (!resp.ok) return { ok: false, generating: false, taskId: null, position: 0, state: null, elapsedSeconds: null };
      const data = (await resp.json()) as {
        generating?: boolean;
        task_id?: string | null;
        position?: number;
        state?: string;
        elapsed_seconds?: number;
      };
      return {
        ok: true,
        generating: data.generating || false,
        taskId: data.task_id || null,
        position: data.position || 0,
        state: data.state || null,
        elapsedSeconds: data.elapsed_seconds || null,
      };
    } catch {
      return { ok: false, generating: false, taskId: null, position: 0, state: null, elapsedSeconds: null };
    }
  },

  /** Poll a background task status by task_id. */
  async pollTaskStatus(
    taskId: string,
  ): Promise<{
    ok: boolean;
    status: string;
    state: string;
    result: Record<string, unknown> | null;
    error: string | null;
  }> {
    try {
      const resp = await fetch(
        `${API_BASE}/pipeline/status/${encodeURIComponent(taskId)}`,
        { headers: authHeaders(), signal: AbortSignal.timeout(5000) },
      );
      if (!resp.ok) return { ok: false, status: "error", state: "unknown", result: null, error: `HTTP ${resp.status}` };
      const data = (await resp.json()) as {
        status?: string;
        state?: string;
        result?: Record<string, unknown>;
        error?: string;
      };
      return {
        ok: true,
        status: data.status || "unknown",
        state: data.state || "unknown",
        result: data.result || null,
        error: data.error || null,
      };
    } catch {
      return { ok: false, status: "error", state: "unknown", result: null, error: "Serveur non disponible" };
    }
  },

  /** Fetch a clinical report (snapshot or progress mode) — triggers LLM generation. */
  async fetchReport(
    participantId: string,
    compare = false,
  ): Promise<{
    ok: boolean;
    reportText: string;
    method: string;
    structured: Record<string, unknown> | null;
    validation: { valid: boolean; issues: string[] };
    error?: string;
  }> {
    try {
      const url = `${API_BASE}/report/${encodeURIComponent(participantId)}${compare ? "?compare=true" : ""}`;
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify([]),
        signal: AbortSignal.timeout(90000),
      });
      if (!resp.ok) {
        return {
          ok: false, reportText: "", method: "error",
          structured: null, validation: { valid: false, issues: [] },
          error: await getResponseErrorMessage(resp),
        };
      }
      const data = (await resp.json()) as {
        report_text?: string;
        method?: string;
        structured?: Record<string, unknown>;
        validation?: { valid: boolean; issues: string[] };
      };
      return {
        ok: true,
        reportText: data.report_text || "",
        method: data.method || "template",
        structured: data.structured || null,
        validation: data.validation || { valid: true, issues: [] },
      };
    } catch {
      return {
        ok: false, reportText: "", method: "error",
        structured: null, validation: { valid: false, issues: [] },
        error: "Serveur non disponible",
      };
    }
  },
};
