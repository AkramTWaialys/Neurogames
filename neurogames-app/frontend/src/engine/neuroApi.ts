/**
 * neuroApi.ts — MLOps server communication (port 8000).
 *
 * Port of web_frontend/js/api.js → TypeScript.
 * Game engines upload sessions to the single MLOps backend.
 */

import type { Player } from "./types";
import { authHeaders } from "../lib/authApi";
import { getResponseErrorMessage } from "../lib/apiErrors";
import { CONFIG } from "../lib/config";

const API_BASE = CONFIG.API_BASE_URL;

interface BasePayload {
  Participant_ID: string;
  Game_Session_ID: string;
  Age: number;
  Age_Group: string;
  Cognitive_Level: string;
  Game_Completion_Status: string;
  Performance_Level: string;
  Time_Spent: number;
  Total_Actions: number;
  Correct_Responses: number;
  Incorrect_Responses: number;
  Hint_Usage?: number;
  Touch_Interactions: number;
  Reaction_Time: number;
  level: number;
  max_level_reached: number;
  cluster: string;
  played_at: string;
  rounds_played: number;
  rounds_passed: number;
  session_outcome: string;
  round_details: string;
}

interface SessionMetrics {
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
  max_level_reached?: number;
}

function perfLevel(correct: number, total: number): string {
  if (total === 0) return "Disengaged";
  const acc = correct / total;
  if (acc >= 0.75) return "Optimal";
  if (acc >= 0.45) return "Struggling";
  return "Disengaged";
}

function cogLevel(raw: string): string {
  const lo = raw.toLowerCase();
  if (lo === "high" || lo === "advanced") return "Advanced";
  if (lo === "low" || lo === "early") return "Early";
  return "Developing";
}

export const NeuroAPI = {
  async uploadSession(gameName: string, payload: Record<string, unknown>) {
    try {
      const resp = await fetch(`${API_BASE}/upload/${gameName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const message = await getResponseErrorMessage(resp);
        console.error("[NeuroAPI] Upload failed:", message);
        return { ok: false, message };
      }
      const data = (await resp.json()) as { session_id: string };
      return { ok: true, message: `Session ${data.session_id} sauvegardée ✓` };
    } catch (e) {
      console.warn("[NeuroAPI] Server unreachable — data not saved", e);
      return { ok: false, message: "Serveur non disponible (mode hors-ligne)" };
    }
  },

  async ping(): Promise<boolean> {
    try {
      const resp = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
      return resp.ok;
    } catch {
      return false;
    }
  },

  buildBase(player: Player, metrics: SessionMetrics): BasePayload {
    const payload: BasePayload = {
      Participant_ID:         player.id,
      Game_Session_ID:        `${player.id}_${Date.now()}`,
      Age:                    player.age,
      Age_Group:              player.ageGroup,
      Cognitive_Level:        cogLevel(player.cognitiveLevel),
      Game_Completion_Status: metrics.completed ? "Completed" : "Not Completed",
      Performance_Level:      perfLevel(metrics.correct, metrics.totalActions),
      Time_Spent:             Math.round(metrics.duration_s * 10) / 10,
      Total_Actions:          metrics.totalActions,
      Correct_Responses:      metrics.correct,
      Incorrect_Responses:    metrics.incorrect,
      Touch_Interactions:     metrics.totalActions,
      Reaction_Time:          Math.round((metrics.avgRT_s || 0) * 1000) / 1000,
      level:                  metrics.level || 1,
      max_level_reached:      metrics.max_level_reached || metrics.level || 1,
      cluster:                "Unknown",
      played_at:              new Date().toISOString(),
      rounds_played:          metrics.roundsPlayed || 1,
      rounds_passed:          metrics.roundsPassed || 0,
      session_outcome:        metrics.sessionOutcome || (metrics.completed ? "completed" : "failed_at_round_1"),
      round_details:          JSON.stringify(metrics.roundDetails || []),
    };
    if (typeof metrics.hintUsage === "number") {
      payload.Hint_Usage = metrics.hintUsage;
    }
    return payload;
  },
};
