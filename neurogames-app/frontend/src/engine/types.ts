/**
 * types.ts — Shared type definitions for the NeuroGames engine.
 */

export interface Player {
  id: string;
  age: number;
  ageGroup: "6-8" | "9-11" | "12-14";
  cognitiveLevel: string;
  adhdSubtype: "inattentive" | "hyperactive" | "combined" | null;
}

export interface DisplayMetric {
  label: string;
  value: string | number;
}

export interface GameResults {
  gameName: string;
  accuracy: number;
  level?: number;
  payload: Record<string, unknown>;
  displayMetrics: DisplayMetric[];
}

export interface RoundPassedInfo {
  round: number;
  maxRounds: number;
  accuracy: number;
  level: number;
  displayMetrics: DisplayMetric[];
}

/**
 * CoreCallbacks — the "core" interface every game class receives.
 *
 * This contract decouples games from the UI framework.
 * GameCanvas.tsx implements this to bridge React ↔ canvas games.
 */
export interface CoreCallbacks {
  /** Show brief ✓ or ✗ feedback overlay */
  showFeedback(correct: boolean): void;

  /** Set the current level display */
  setLevel(level: number): void;

  /** Set the score display */
  setScore(score: number): void;

  /** Set the progress bar (0–1) */
  _setProgress(pct: number): void;

  /** Show a floating toast message */
  toast(msg: string, color: string): void;

  /** Spawn particle celebration */
  spawnParticles(): void;

  /** Play a named sound effect (correct, wrong, levelup, click, tick, combo) */
  playSound(name: string): void;

  /** Called when a round is passed (multi-round games) */
  onRoundPassed(info: RoundPassedInfo): void;

  /** Called when a round is failed */
  onRoundFailed(results: GameResults): void;

  /** Called when the entire game session completes */
  onGameComplete(results: GameResults): void;

  /** Resume game after a round pause */
  continueNextRound(): void;

  /** Translate a display-only UI/canvas key in the active language. */
  t(key: string, params?: Record<string, string | number | boolean | null | undefined>): string;

  /** Current locale and direction, read dynamically by canvas games. */
  getLocale(): "fr" | "en" | "ar";
  getDir(): "ltr" | "rtl";
}

/**
 * GameInstance — the interface every game class must implement.
 */
export interface GameInstance {
  start(): void;
  stop(): void;
  onResize(): void;
  /** Resume next round (called by core after round pause UI) */
  _startRound?: () => void;
  /** Pause the game loop */
  pause?: () => void;
  /** Resume the game loop */
  resume?: () => void;
  /** Get metrics of an active partial game for interruption saves */
  getPartialResults?: () => GameResults | null;
}
