import { NeuroAPI } from "./api";
import { HistoryManager } from "./history";

/**
 * SessionEngine — Manages contiguous session data accumulation across levels.
 */

/** Canonical game metadata for localStorage entries */
const GAME_META: Record<string, { name: string; icon: string }> = {
  gonogo:   { name: "Go / No-Go",       icon: "🎯" },
  memory:   { name: "Memory Match",     icon: "🃏" },
  tracking: { name: "Visual Tracking",  icon: "👁️" },
  shapes:   { name: "Shape Builder",    icon: "🔷" },
  puzzle:   { name: "Puzzle Quest",     icon: "🧩" },
};

interface SessionCumulativeData {
  gameKey: string;
  player: any;
  duration_s: number;
  totalActions: number;
  correct: number;
  incorrect: number;
  avgRTs: number[]; // Store raw averages to compute weighted avg later
  roundsPlayed: number;
  roundsPassed: number;
  roundDetails: Record<string, unknown>[];
  startTime: number;
  maxLevelReached: number; // Track the highest level the player reached
  hintUsage?: number;
}

const SESSION_STORAGE_KEY = "neuro_pending_session";

function getPending(): SessionCumulativeData | null {
  if (typeof window === "undefined") return null;
  const str = sessionStorage.getItem(SESSION_STORAGE_KEY);
  return str ? JSON.parse(str) : null;
}

function setPending(data: SessionCumulativeData | null) {
  if (typeof window === "undefined") return;
  if (data) {
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(data));
  } else {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  }
}

export const SessionEngine = {
  /** Check if there's a leftover session from a crashed/reloaded tab and commit it. */
  async commitAbandonedSession() {
    const pending = getPending();
    if (pending) {
      setPending(null);
      await this._commit(pending, "abandoned_reload");
    }
  },

  /** Discard the current contiguous session. */
  clearSession() {
    setPending(null);
  },

  /** Push metrics from a completed level into the contiguous session. */
  pushLevel(gameKey: string, player: any, pydanticPayload: any) {
    const pending = getPending();
    const data: SessionCumulativeData = pending || {
      gameKey,
      player,
      duration_s: 0,
      totalActions: 0,
      correct: 0,
      incorrect: 0,
      avgRTs: [],
      roundsPlayed: 0,
      roundsPassed: 0,
      roundDetails: [],
      startTime: Date.now(),
      maxLevelReached: 1,
    };

    data.duration_s += pydanticPayload.Time_Spent || 0;
    data.totalActions += pydanticPayload.Total_Actions || 0;
    data.correct += pydanticPayload.Correct_Responses || 0;
    data.incorrect += pydanticPayload.Incorrect_Responses || 0;
    if (typeof pydanticPayload.Hint_Usage === "number") {
      data.hintUsage = (data.hintUsage || 0) + pydanticPayload.Hint_Usage;
    }
    if (typeof pydanticPayload.Reaction_Time === "number") {
      data.avgRTs.push(pydanticPayload.Reaction_Time);
    }
    data.roundsPlayed += pydanticPayload.rounds_played || 1;
    data.roundsPassed += pydanticPayload.rounds_passed || 0;
    
    let rDetails = pydanticPayload.round_details || [];
    if (typeof rDetails === "string") {
      try { rDetails = JSON.parse(rDetails); } catch { rDetails = []; }
    }
    data.roundDetails.push(...rDetails);

    // Track the highest level from the game's payload
    const payloadLevel = pydanticPayload.max_level_reached || pydanticPayload.level || 1;
    data.maxLevelReached = Math.max(data.maxLevelReached, payloadLevel);

    setPending(data);
  },

  /** Commit the contiguous session — fires on Failure or Quit. */
  async commitSession(outcome: string) {
    const pending = getPending();
    if (!pending) return;
    setPending(null);
    await this._commit(pending, outcome);
  },

  /** Internal unified commit method that uploads payload & history. */
  async _commit(data: SessionCumulativeData, outcome: string) {
    const totalRT = data.avgRTs.reduce((a, b) => a + b, 0);
    const avgRT = data.avgRTs.length > 0 ? totalRT / data.avgRTs.length : 0;
    const accuracy = data.correct / Math.max(data.totalActions, 1);
    
    // Build the consolidated API payload
    const basePayload = NeuroAPI.buildBase(data.player, {
      duration_s: data.duration_s,
      totalActions: data.totalActions,
      correct: data.correct,
      incorrect: data.incorrect,
      avgRT_s: avgRT,
      completed: outcome === "completed",
      roundsPlayed: data.roundsPlayed,
      roundsPassed: data.roundsPassed,
      sessionOutcome: outcome,
      roundDetails: data.roundDetails,
      level: data.maxLevelReached,
      ...(typeof data.hintUsage === "number" ? { hintUsage: data.hintUsage } : {}),
    });

    const finalPayload = {
      ...basePayload,
      hits: data.correct,
      misses: data.incorrect,
      max_level_reached: data.maxLevelReached,
    };

    if (data.gameKey === 'gonogo') {
      // Approximate hits and misses specifically for history if needed, 
      // but payload hits/misses cover it.
      // D-prime and errors are hard to perfectly recalculate without all arrays, 
      // but total hits/incorrect are enough for basic ML drift processing.
    }

    // 1. Upload
    await NeuroAPI.uploadSession(data.gameKey, finalPayload);

    // 2. Save to HistoryManager with proper game metadata
    const meta = GAME_META[data.gameKey] || { name: data.gameKey, icon: "🎮" };
    const stars = accuracy >= 0.75 ? "⭐⭐⭐" : accuracy >= 0.45 ? "⭐⭐" : "⭐";
    const perfLevel = accuracy >= 0.75 ? "Optimal" : accuracy >= 0.45 ? "Struggling" : "Disengaged";

    HistoryManager.push({
      id: `${data.gameKey}_${Date.now()}`,
      game: data.gameKey,
      gameName: meta.name,
      icon: meta.icon,
      timestamp: Date.now(),
      accuracy,
      stars,
      level: finalPayload.max_level_reached,
      duration_s: data.duration_s,
      correct: data.correct,
      totalActions: data.totalActions,
      reactionTime: Math.round(avgRT * 1000) / 1000,
      performanceLevel: perfLevel,
      displayMetrics: [
        { label: "Correct / Total", value: `${data.correct} / ${data.totalActions}` },
        { label: "Précision", value: `${Math.round(accuracy * 100)}%` },
        { label: "Temps réaction", value: `${Math.round(avgRT * 1000)}ms` },
        { label: "Rounds joués", value: data.roundsPlayed },
      ],
    });
  }
};
