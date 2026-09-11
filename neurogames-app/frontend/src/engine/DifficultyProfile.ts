/**
 * DifficultyProfile.ts — Adaptive difficulty engine.
 */

import type { Player } from "./types";

const inMemoryStore = new Map<string, any>();
export class DifficultyProfile {
  level: number;
  stimulus_duration: number;
  inter_stimulus_interval: number;
  n_stimuli_per_session: number;
  distractor_ratio: number;
  feedback_duration: number;
  time_pressure: boolean;
  colorblind_mode: boolean;
  // Memory-specific
  grid_cols: number;
  grid_rows: number;
  card_preview_duration: number;
  // Tracking-specific
  target_speed: number;
  target_radius: number;

  _autoSaveKey: [string, Player] | null = null;

  constructor(opts: Partial<DifficultyProfile> = {}) {
    this.level                   = opts.level ?? 1;
    this.stimulus_duration       = opts.stimulus_duration ?? 1.5;
    this.inter_stimulus_interval = opts.inter_stimulus_interval ?? 2.0;
    this.n_stimuli_per_session   = opts.n_stimuli_per_session ?? 10;
    this.distractor_ratio        = opts.distractor_ratio ?? 0.3;
    this.feedback_duration       = opts.feedback_duration ?? 0.8;
    this.time_pressure           = opts.time_pressure ?? false;
    this.colorblind_mode         = opts.colorblind_mode ?? false;
    this.grid_cols               = opts.grid_cols ?? 3;
    this.grid_rows               = opts.grid_rows ?? 2;
    this.card_preview_duration   = opts.card_preview_duration ?? 2.0;
    this.target_speed            = opts.target_speed ?? 70.0;
    this.target_radius           = opts.target_radius ?? 47;
  }

  increase(): void {
    this.level = Math.min(10, this.level + 1);
    this.stimulus_duration        = +Math.max(0.4, this.stimulus_duration - 0.1).toFixed(2);
    this.inter_stimulus_interval  = +Math.max(0.8, this.inter_stimulus_interval - 0.1).toFixed(2);
    this.distractor_ratio         = +Math.min(0.6, this.distractor_ratio + 0.05).toFixed(3);

    if (this.level === 4 || this.level === 7) {
      this.grid_cols = Math.min(4, this.grid_cols + 1);
      if (this.level === 7) this.grid_rows = Math.min(4, this.grid_rows + 1);
      this.card_preview_duration = +Math.max(0.5, this.card_preview_duration - 0.5).toFixed(2);
    }

    this.target_speed  = Math.min(200, this.target_speed + 15);
    this.target_radius = Math.max(20, this.target_radius - 3);
  }

  decrease(): void {
    this.level = Math.max(1, this.level - 1);
    this.stimulus_duration        = +Math.min(3.0, this.stimulus_duration + 0.2).toFixed(2);
    this.inter_stimulus_interval  = +Math.min(3.0, this.inter_stimulus_interval + 0.15).toFixed(2);
    this.distractor_ratio         = +Math.max(0.1, this.distractor_ratio - 0.05).toFixed(3);
    this.target_speed  = Math.max(50, this.target_speed - 10);
    this.target_radius = Math.min(50, this.target_radius + 3);
  }

  adapt(correct: number, total: number, avgRtMs = 999): "increased" | "decreased" | "stable" {
    const accuracy = correct / Math.max(total, 1);
    const avg_rt   = avgRtMs || 999;

    let result: "increased" | "decreased" | "stable";
    if (accuracy >= 0.80 && avg_rt < 1500) {
      this.increase();
      result = "increased";
    } else if (accuracy < 0.50 || avg_rt > 3000) {
      this.decrease();
      result = "decreased";
    } else {
      result = "stable";
    }

    if (this._autoSaveKey) this.save(this._autoSaveKey[0], this._autoSaveKey[1]);
    return result;
  }

  clone(): DifficultyProfile {
    return new DifficultyProfile({ ...this });
  }

  static load(gameKey: string, player: Player): DifficultyProfile {
    const key = `neuro_diff_${gameKey}_${player.id}`;
    const stored = inMemoryStore.get(key);
    if (stored) {
      const p = new DifficultyProfile();
      Object.assign(p, stored);
      p._autoSaveKey = [gameKey, player];
      return p;
    }
    const profile = new DifficultyProfile();
    profile._autoSaveKey = [gameKey, player];
    return profile;
  }

  save(gameKey: string, player: Player): void {
    const key = `neuro_diff_${gameKey}_${player.id}`;
    inMemoryStore.set(key, { ...this });
  }

  static reset(gameKey: string, player: Player): void {
    const key = `neuro_diff_${gameKey}_${player.id}`;
    inMemoryStore.delete(key);
  }
}
