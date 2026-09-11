/**
 * GoNoGoGame.ts — Go/No-Go Cognitive Game.
 *
 * Faithful TypeScript port of web_frontend/js/games/GoNoGoGame.js.
 * State machine: fixation → stimulus → response_window → feedback → (loop) → finished
 */

import { DifficultyProfile } from "../DifficultyProfile";
import { NeuroAPI } from "../neuroApi";
import { roundRect, lerpColorRGB, shuffle, maxRun } from "../utils";
import type { CoreCallbacks, Player, GameInstance } from "../types";

export class GoNoGoGame implements GameInstance {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private player: Player;
  private core: CoreCallbacks;

  private diff: DifficultyProfile;
  private MAX_ROUNDS = 3;
  private currentRound = 0;

  private sessionTotalCorrect = 0;
  private sessionTotalTrials = 0;
  private sessionAvgRtMs = 0;

  private sequence: string[] = [];
  private currentTrial = 0;

  private hits = 0;
  private misses = 0;
  private falseAlarms = 0;
  private correctRejections = 0;
  private reactionTimes: number[] = [];
  private totalGo = 0;
  private totalNogo = 0;

  private roundHits = 0;
  private roundMisses = 0;
  private roundFA = 0;
  private roundCR = 0;
  private roundRTs: number[] = [];
  private _roundDetails: Record<string, unknown>[] = [];

  private state = "fixation";
  private timer = 0;
  private responded = false;
  private stimStartMs = 0;
  private windowElapsed = 0;

  private stimScale = 1;
  private pulsePhase = 0;

  private sessionStartMs = 0;
  private _raf: number | null = null;
  private _lastTs: number | null = null;
  private _running = false;
  private _paused = false;

  private _onKeyDown: (e: KeyboardEvent) => void;
  private _onPointer: () => void;

  constructor(canvas: HTMLCanvasElement, player: Player, core: CoreCallbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.player = player;
    this.core = core;
    this.diff = DifficultyProfile.load("gonogo", player);
    this._onKeyDown = this._handleKey.bind(this);
    this._onPointer = this._handlePointer.bind(this);
  }

  start(): void {
    this.currentRound = 0;
    this.hits = 0; this.misses = 0;
    this.falseAlarms = 0; this.correctRejections = 0;
    this.reactionTimes = [];
    this.totalGo = 0; this.totalNogo = 0;
    this.sessionTotalCorrect = 0;
    this.sessionTotalTrials = 0;
    this.sessionAvgRtMs = 0;
    this.sessionStartMs = Date.now();
    this._running = true;
    this.core.setLevel(this.diff.level);

    window.addEventListener("keydown", this._onKeyDown);
    this.canvas.addEventListener("pointerdown", this._onPointer);

    this._startRound();
    this._lastTs = performance.now();
    this._loop(this._lastTs);
  }

  _startRound(): void {
    this.sequence = this._generateSequence();
    this.currentTrial = 0;
    this.roundHits = 0; this.roundMisses = 0;
    this.roundFA = 0; this.roundCR = 0;
    this.roundRTs = [];

    this.totalGo += this.sequence.filter(x => x === "go").length;
    this.totalNogo += this.sequence.filter(x => x === "nogo").length;

    this.state = "fixation";
    this.responded = false;
    this.timer = this.diff.inter_stimulus_interval * 1000;

    this.core.setLevel(this.diff.level);
    this.core._setProgress(this.currentRound / this.MAX_ROUNDS);
  }

  stop(): void {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    window.removeEventListener("keydown", this._onKeyDown);
    this.canvas.removeEventListener("pointerdown", this._onPointer);
  }

  onResize(): void {}

  pause(): void { this._paused = true; }
  resume(): void { this._paused = false; this._lastTs = performance.now(); }

  private _generateSequence(): string[] {
    const n = this.diff.n_stimuli_per_session;
    const nNogo = Math.max(1, Math.floor(n * this.diff.distractor_ratio));
    const nGo = n - nNogo;
    let seq = [...Array(nGo).fill("go"), ...Array(nNogo).fill("nogo")];
    for (let attempt = 0; attempt < 100; attempt++) {
      seq = shuffle(seq);
      if (maxRun(seq, "nogo") <= 3) break;
    }
    return seq;
  }

  private _loop(ts: number): void {
    if (!this._running) return;
    if (this._paused) { this._raf = requestAnimationFrame(t => this._loop(t)); return; }
    const dt = ts - (this._lastTs ?? ts);
    this._lastTs = ts;
    this._update(dt);
    this._draw();
    this._raf = requestAnimationFrame(t => this._loop(t));
  }

  private _update(dt: number): void {
    this.timer -= dt;
    this.pulsePhase += dt * 0.003;

    switch (this.state) {
      case "fixation":
        if (this.timer <= 0) {
          this.state = "stimulus";
          this.responded = false;
          this.timer = this.diff.stimulus_duration * 1000;
          this.stimStartMs = performance.now();
          this.stimScale = 0.1;
        }
        break;

      case "stimulus":
        this.stimScale = Math.min(1, this.stimScale + dt * 0.010);
        if (this.timer <= 0 && !this.responded) {
          this.state = "response_window";
          this.timer = Math.max(300, this.diff.inter_stimulus_interval * 500);
          this.windowElapsed = 0;
        }
        break;

      case "response_window":
        this.windowElapsed += dt;
        if (this.timer <= 0 && !this.responded) {
          const trialType = this.sequence[this.currentTrial];
          if (trialType === "go") {
            this.misses++; this.roundMisses++;
            this.core.showFeedback(false);
          } else {
            this.correctRejections++; this.roundCR++;
            this.core.showFeedback(true);
          }
          this.state = "feedback";
          this.timer = this.diff.feedback_duration * 1000;
        }
        break;

      case "feedback":
        if (this.timer <= 0) {
          this.currentTrial++;
          if (this.currentTrial >= this.sequence.length) {
            this.currentRound++;
            const roundTotal = this.roundHits + this.roundMisses + this.roundFA + this.roundCR;
            const roundCorrect = this.roundHits + this.roundCR;
            const avgRoundRt = this.roundRTs.length
              ? this.roundRTs.reduce((a, b) => a + b, 0) / this.roundRTs.length
              : 999;
            this.sessionTotalCorrect += roundCorrect;
            this.sessionTotalTrials += roundTotal;
            this.sessionAvgRtMs = this.sessionTotalTrials > 0
              ? (this.sessionAvgRtMs * (this.sessionTotalTrials - roundTotal) + avgRoundRt * roundTotal) / this.sessionTotalTrials
              : avgRoundRt;

            const roundAccuracy = roundCorrect / Math.max(roundTotal, 1);

            if (roundAccuracy < 0.45) {
              this.state = "finished";
              this._failRound(roundAccuracy);
            } else if (this.currentRound >= this.MAX_ROUNDS) {
              this.state = "finished";
              this._endSession();
            } else {
              this.state = "round_pause";
              this._passRound(roundAccuracy);
            }
          } else {
            this.state = "fixation";
            this.responded = false;
            this.timer = this.diff.inter_stimulus_interval * 1000;
          }

          const overallProgress = (this.currentRound + this.currentTrial / this.sequence.length) / this.MAX_ROUNDS;
          this.core._setProgress(overallProgress);
          this.core.setScore(this.hits);
        }
        break;
    }
  }

  private _handleKey(e: KeyboardEvent): void {
    if (e.code === "Space") { e.preventDefault(); this._respond(); }
  }

  private _handlePointer(): void {
    this._respond();
  }

  private _respond(): void {
    if (this.responded) return;
    if (this.state !== "stimulus" && this.state !== "response_window") return;

    this.responded = true;
    const trialType = this.sequence[this.currentTrial];
    const rtMs = Math.round(performance.now() - this.stimStartMs);

    if (trialType === "go") {
      this.hits++; this.roundHits++;
      this.reactionTimes.push(rtMs);
      this.roundRTs.push(rtMs);
      this.core.showFeedback(true);
    } else {
      this.falseAlarms++; this.roundFA++;
      this.core.showFeedback(false);
    }

    this.state = "feedback";
    this.timer = this.diff.feedback_duration * 1000;
  }

  private _draw(): void {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;


    ctx.fillStyle = "#f5f7fa";
    ctx.fillRect(0, 0, W, H);
    this._drawHUD(ctx, W);

    switch (this.state) {
      case "fixation":
        this._drawFixation(ctx, cx, cy);
        break;
      case "stimulus":
      case "response_window":
        this._drawStimulus(ctx, cx, cy, false);
        break;
      case "feedback":
        this._drawStimulus(ctx, cx, cy, true);
        break;
      case "finished":
        this._drawFinished(ctx, cx, cy);
        break;
    }

    // Segmented trial dots (replaces thin progress bar)
    const n = Math.max(this.sequence.length, 1);
    const dotR = 5;
    const dotGap = 14;
    const totalDotsW = n * dotGap;
    const dotStartX = (W - totalDotsW) / 2;
    const dotY = H - 18;
    for (let i = 0; i < n; i++) {
      ctx.beginPath();
      ctx.arc(dotStartX + i * dotGap + dotGap / 2, dotY, dotR, 0, Math.PI * 2);
      if (i < this.currentTrial) {
        ctx.fillStyle = '#22c55e';
      } else if (i === this.currentTrial) {
        ctx.fillStyle = '#6D28D9';
      } else {
        ctx.fillStyle = '#dde0ea';
      }
      ctx.fill();
    }
  }

  private _drawHUD(ctx: CanvasRenderingContext2D, W: number): void {
    ctx.save();
    ctx.fillStyle = "rgba(30,35,50,0.06)"; ctx.fillRect(0, 0, W, 55);
    ctx.font = "bold 20px Nunito, sans-serif"; ctx.textBaseline = "middle";
    const n = this.sequence.length || 1;
    ctx.fillStyle = "#28283a"; ctx.textAlign = "left";
    ctx.fillText(this.core.t("engine.targetCounter", { current: Math.min(this.currentTrial + 1, n), total: n, round: this.currentRound + 1, max: this.MAX_ROUNDS }), 30, 28);
    ctx.restore();
  }

  private _drawFixation(ctx: CanvasRenderingContext2D, cx: number, cy: number): void {
    ctx.save();
    // Animated concentric pulsing rings
    const phase = this.pulsePhase;
    for (let i = 0; i < 3; i++) {
      const r = 20 + i * 22 + Math.sin(phase + i * 1.2) * 6;
      const alpha = 0.15 + 0.1 * Math.sin(phase + i);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(109, 40, 217, ${alpha})`;
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }
    // Center dot
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(109, 40, 217, 0.5)';
    ctx.fill();
    // Text
    ctx.font = '20px Nunito, sans-serif';
    ctx.fillStyle = 'rgba(140,145,160,0.6)';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(this.core.t("engine.ready"), cx, cy + 80);
    ctx.restore();
  }

  private _drawStimulus(ctx: CanvasRenderingContext2D, cx: number, cy: number, faded: boolean): void {
    if (this.currentTrial >= this.sequence.length) return;
    const trialType = this.sequence[this.currentTrial];
    const isGo = trialType === "go";
    const size = 160;
    const r = (size / 2) * this.stimScale;
    const alpha = faded ? 0.3 : 1;
    // Brighter, more saturated colors for ADHD visibility
    const color = isGo ? "#22c55e" : "#ef4444";

    ctx.save();
    ctx.globalAlpha = alpha;

    // Dark vignette behind stimulus for contrast
    if (!faded) {
      const grad = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r * 3);
      grad.addColorStop(0, 'rgba(0,0,0,0.08)');
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = grad;
      ctx.fillRect(cx - r * 3, cy - r * 3, r * 6, r * 6);
    }

    if (!faded) {
      ctx.save();
      ctx.globalAlpha = 0.12;
      ctx.beginPath(); ctx.arc(cx, cy + 5, r + 5, 0, Math.PI * 2);
      ctx.fillStyle = "#000"; ctx.fill();
      ctx.restore();
    }

    // Snappier overshoot pulse
    const pulse = faded ? 0 : Math.sin(this.pulsePhase) * 6;
    ctx.beginPath(); ctx.arc(cx, cy, r + pulse, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.restore();

    if (!faded && this.diff.level <= 2) {
      const actionText = isGo ? this.core.t("engine.pressSpace") : this.core.t("engine.dontMove");
      const actionColor = isGo ? "#22c55e" : "#ef4444";
      ctx.save();
      ctx.font = "bold 24px Nunito, sans-serif";
      ctx.fillStyle = actionColor;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(actionText, cx, cy + 130);
      ctx.restore();
    }
  }

  private _drawFinished(ctx: CanvasRenderingContext2D, cx: number, cy: number): void {
    ctx.save();
    ctx.font = "bold 28px Nunito, sans-serif";
    ctx.fillStyle = "#5a78dc";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(this.core.t("engine.sessionComplete"), cx, cy);
    ctx.restore();
  }

  private _failRound(roundAccuracy: number): void {
    // Record this failed round in _roundDetails
    const avgRt = this.roundRTs.length
      ? Math.round(this.roundRTs.reduce((a, b) => a + b, 0) / this.roundRTs.length) : 0;
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound + 1, passed: false, accuracy: roundAccuracy,
      hits: this.roundHits, misses: this.roundMisses,
      false_alarms: this.roundFA, correct_rejections: this.roundCR,
      avg_rt_ms: avgRt,
    });

    // Build complete session payload (same structure as _endSession)
    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const total = this.hits + this.misses + this.falseAlarms + this.correctRejections;
    const accuracy = (this.hits + this.correctRejections) / Math.max(total, 1);
    const overallAvgRt = this.reactionTimes.length
      ? this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length : 0;

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: total,
      correct: this.hits + this.correctRejections,
      incorrect: this.misses + this.falseAlarms,
      avgRT_s: overallAvgRt / 1000,
      completed: false,
      roundsPlayed: this.currentRound + 1,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: `failed_at_round_${this.currentRound + 1}`,
      roundDetails: this._roundDetails,
      level: this.diff.level,
    });

    const nGo = Math.max(1, this.totalGo);
    const nNogo = Math.max(1, this.totalNogo);
    const hitRate = this.hits / nGo;
    const faRate = this.falseAlarms / nNogo;

    const payload = {
      ...base,
      hits: this.hits, misses: this.misses,
      false_alarms: this.falseAlarms, correct_rejections: this.correctRejections,
      commission_error_rate: Math.round(faRate * 10000) / 10000,
      omission_error_rate: Math.round((this.misses / nGo) * 10000) / 10000,
      d_prime_approx: Math.round((hitRate - faRate) * 10000) / 10000,
      rt_variability_ms: 0,
      max_level_reached: this.diff.level,
    };

    const results = {
      gameName: "gonogo",
      accuracy,
      level: this.diff.level,
      payload,
      displayMetrics: [
        { label: "✅ Réussites", value: this.roundHits + this.roundCR },
        { label: "❌ Erreurs", value: this.roundMisses + this.roundFA },
        { label: "📊 Précision", value: `${Math.round(roundAccuracy * 100)} %` },
        { label: "💡 Conseil", value: this.core.t("engine.gonogoAdvice") },
      ],
    };
    // Decrease difficulty on failure + save
    this.diff.decrease();
    this.diff.save("gonogo", this.player);
    setTimeout(() => this.core.onGameComplete(results), 1200);
  }

  private _passRound(roundAccuracy: number): void {
    const avgRt = this.roundRTs.length
      ? Math.round(this.roundRTs.reduce((a, b) => a + b, 0) / this.roundRTs.length)
      : 0;
    // Record passed round in _roundDetails
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound, passed: true, accuracy: roundAccuracy,
      hits: this.roundHits, misses: this.roundMisses,
      false_alarms: this.roundFA, correct_rejections: this.roundCR,
      avg_rt_ms: avgRt,
    });
    this.core.onRoundPassed({
      round: this.currentRound,
      maxRounds: this.MAX_ROUNDS,
      accuracy: roundAccuracy,
      level: this.diff.level,
      displayMetrics: [
        { label: "✅ Réussites", value: this.roundHits + this.roundCR },
        { label: "❌ Erreurs", value: this.roundMisses + this.roundFA },
        { label: "📊 Précision", value: `${Math.round(roundAccuracy * 100)} %` },
        { label: "⏱️ RT moyen", value: avgRt ? `${avgRt} ms` : "—" },
      ],
    });
  }

  private _endSession(): void {
    const completedLevel = this.diff.level;
    this.diff.increase();
    this.core.toast(this.core.t("engine.nextLevelToast"), "#50c878");
    this.diff.save("gonogo", this.player);

    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const nGo = Math.max(1, this.totalGo);
    const nNogo = Math.max(1, this.totalNogo);
    const total = this.hits + this.misses + this.falseAlarms + this.correctRejections;

    const hitRate = this.hits / nGo;
    const faRate = this.falseAlarms / nNogo;
    const dPrime = hitRate - faRate;

    let rtStd = 0;
    if (this.reactionTimes.length > 1) {
      const m = this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length;
      rtStd = Math.sqrt(
        this.reactionTimes.reduce((a, b) => a + (b - m) ** 2, 0) / (this.reactionTimes.length - 1)
      );
    }
    const avgRt = this.reactionTimes.length
      ? this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length : 0;
    const accuracy = (this.hits + this.correctRejections) / Math.max(total, 1);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: total,
      correct: this.hits + this.correctRejections,
      incorrect: this.misses + this.falseAlarms,
      avgRT_s: avgRt / 1000,
      completed: true,
      roundsPlayed: this.MAX_ROUNDS,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: "completed",
      roundDetails: this._roundDetails,
      level: completedLevel,
    });

    const payload = {
      ...base,
      hits: this.hits,
      misses: this.misses,
      false_alarms: this.falseAlarms,
      correct_rejections: this.correctRejections,
      commission_error_rate: Math.round(faRate * 10000) / 10000,
      omission_error_rate: Math.round((this.misses / nGo) * 10000) / 10000,
      d_prime_approx: Math.round(dPrime * 10000) / 10000,
      rt_variability_ms: Math.round(rtStd * 100) / 100,
      max_level_reached: completedLevel,
    };

    const results = {
      gameName: "gonogo",
      accuracy,
      level: completedLevel,
      payload,
      displayMetrics: [
        { label: "✅ Hits", value: this.hits },
        { label: "❌ Fausses alarmes", value: this.falseAlarms },
        { label: "😶 Ratés", value: this.misses },
        { label: "🛡️ Bonnes rejections", value: this.correctRejections },
        { label: "⏱️ RT moyen", value: avgRt ? `${Math.round(avgRt)} ms` : "—" },
        { label: "📊 d'", value: dPrime.toFixed(4) },
        { label: "🎯 Niveau", value: completedLevel },
      ],
    };
    setTimeout(() => this.core.onGameComplete(results), 1200);
  }

  getPartialResults() {
    if (this.state === 'finished') return null;
    const total = this.hits + this.misses + this.falseAlarms + this.correctRejections;
    if (total === 0) return null;

    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const nGo = Math.max(1, this.totalGo);
    const nNogo = Math.max(1, this.totalNogo);

    const hitRate = this.hits / nGo;
    const faRate = this.falseAlarms / nNogo;
    const dPrime = hitRate - faRate;

    let rtStd = 0;
    if (this.reactionTimes.length > 1) {
      const m = this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length;
      rtStd = Math.sqrt(
        this.reactionTimes.reduce((a, b) => a + (b - m) ** 2, 0) / (this.reactionTimes.length - 1)
      );
    }
    const avgRt = this.reactionTimes.length
      ? this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length : 0;
    const accuracy = (this.hits + this.correctRejections) / Math.max(total, 1);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: total,
      correct: this.hits + this.correctRejections,
      incorrect: this.misses + this.falseAlarms,
      avgRT_s: avgRt / 1000,
      completed: false,
      roundsPlayed: this.currentRound,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: "interrupted",
      roundDetails: this._roundDetails,
    });

    const payload = {
      ...base,
      hits: this.hits,
      misses: this.misses,
      false_alarms: this.falseAlarms,
      correct_rejections: this.correctRejections,
      commission_error_rate: Math.round(faRate * 10000) / 10000,
      omission_error_rate: Math.round((this.misses / nGo) * 10000) / 10000,
      d_prime_approx: Math.round(dPrime * 10000) / 10000,
      rt_variability_ms: Math.round(rtStd * 100) / 100,
      max_level_reached: this.diff.level,
    };

    return {
      gameName: "gonogo",
      accuracy,
      level: this.diff.level,
      payload,
      displayMetrics: [],
    };
  }
}
