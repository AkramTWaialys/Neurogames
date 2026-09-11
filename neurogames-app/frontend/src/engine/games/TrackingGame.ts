// @ts-nocheck
import { DifficultyProfile } from '../DifficultyProfile';
import { NeuroAPI } from '../neuroApi';
import { roundRect, lerpColorRGB } from '../utils';

/**
 * TrackingGame.js — Visual Tracking Cognitive Game (faithful web port)
 *
 * Faithful port of tracking/tracking_game.py + tracking/target.py.
 *
 * Mechanics (from source):
 *   - state machine: target_shown → feedback → (next trial)
 *   - total_trials = difficulty.n_stimuli_per_session (20 by default)
 *   - n_distractors = floor(level * distractor_ratio * 2)
 *   - target uses difficulty.target_speed (px/s) and difficulty.target_radius
 *   - only the TARGET circle can be clicked (distractors are ignored/clickable but don't register)
 *   - clicking to the side of everything is also ignored (no punishment, child can re-click)
 *   - timeout (stimulus_duration) → MISS → feedback for feedback_duration → next trial
 *   - distractor color = muted gray; target color = primary blue
 *
 * DifficultyProfile defaults (level 1):
 *   target_speed=70, target_radius=47, n_stimuli=20, distractor_ratio=0.3,
 *   stimulus_duration=1.5s, feedback_duration=0.8s
 *   increase(): speed+15, radius-3, distractor_ratio+0.05
 *
 * Metrics (TrackingSession schema):
 *   hits, misses, hit_rate, avg_rt_ms, rt_std_ms,
 *   avg_target_speed, avg_distractor_count
 */

export class TrackingGame {
  constructor(canvas, player, core) {
    this.canvas = canvas;
    this.ctx    = canvas.getContext('2d');
    this.player = player;
    this.core   = core;

    // ── DifficultyProfile (start based on cognitive level) ────────────────
    this.diff = DifficultyProfile.load('tracking', player);
    
    this.MAX_ROUNDS = 3;
    this.currentRound = 0;

    // ── Session-wide stats for end-of-session adaptation ─────────────────
    this.sessionTotalCorrect = 0;
    this.sessionTotalTrials  = 0;
    this.sessionAvgRtMs      = 0;

    // ── Session Cumulative ────────────────────────────────────────────────
    this.sessionCorrect   = 0;
    this.sessionTotal     = 0;
    this.sessionRTs       = [];

    // ── Round Counters ────────────────────────────────────────────────────
    this.correctCount  = 0;
    this.totalCount    = 0;
    this.reactionTimes = [];  // ms

    // ── Trials ─────────────────────────────────────────────────────────────
    this.currentTrial = 0;
    this.totalTrials  = this.diff.n_stimuli_per_session;

    // ── Objects ────────────────────────────────────────────────────────────
    this.target      = null;
    this.distractors = [];

    // ── State ──────────────────────────────────────────────────────────────
    this.state         = 'target_shown';
    this.timer         = 0;   // ms countdown
    this.stimAppeared  = 0;   // performance.now() when target appeared
    this.clickHandled  = false;

    // ── Feedback ───────────────────────────────────────────────────────────
    this.lastHit       = false;
    this.clickSpark    = null; // { x, y, alpha }

    // ── Loop ───────────────────────────────────────────────────────────────
    this.sessionStartMs = 0;
    this._raf     = null;
    this._lastTs  = null;
    this._running = false;
    this._paused  = false;

    // Motion trail (stores recent target positions)
    this._trail = [];  // [ {x, y, alpha} ]

    // Miss animation state
    this._missFlash = null;  // { x, y, alpha, scale }
    this._roundDetails = [];

    this._onClick = this._handleClick.bind(this);
  }

  // Difficulty profile managed externally

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  start() {
    this.sessionStartMs = Date.now();
    this.currentRound   = 0;
    this.sessionCorrect = 0;
    this.sessionTotal   = 0;
    this.sessionRTs     = [];
    this.sessionTotalCorrect = 0;
    this.sessionTotalTrials  = 0;
    this.sessionAvgRtMs      = 0;

    this._running = true;

    // Show the persisted level immediately on the HUD
    this.core.setLevel(this.diff.level);

    this.canvas.addEventListener('pointerdown', this._onClick);
    
    this._startRound();

    this._lastTs = performance.now();
    this._loop(this._lastTs);
  }
  
  _startRound() {
    this.currentTrial   = 0;
    this.totalTrials    = this.diff.n_stimuli_per_session;
    this.correctCount   = 0;
    this.totalCount     = 0;
    this.reactionTimes  = [];

    this.core.setLevel(this.diff.level);
    const overallProgress = this.currentRound / this.MAX_ROUNDS;
    this.core._setProgress(overallProgress);

    this._spawnNext();
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.canvas.removeEventListener('pointerdown', this._onClick);
  }

  pause() { this._paused = true; }
  resume() { this._paused = false; this._lastTs = performance.now(); }

  onResize() { /* objects adapt on next spawn */ }

  // ── Spawning (mirrors _spawn_next) ────────────────────────────────────────

  _spawnNext() {
    if (this.currentTrial >= this.totalTrials) {
      this.sessionCorrect += this.correctCount;
      this.sessionTotal   += this.totalCount;
      this.sessionRTs.push(...this.reactionTimes);

      // Round accuracy
      const roundAccuracy = this.correctCount / Math.max(this.totalCount, 1);

      // Accumulate stats for end-of-session adaptation
      const avgRt = this.reactionTimes.length 
        ? this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length 
        : 999;
      this.sessionTotalCorrect += this.correctCount;
      this.sessionTotalTrials  += Math.max(this.totalCount, 1);
      this.sessionAvgRtMs = this.sessionTotalTrials > 0
        ? (this.sessionAvgRtMs * (this.sessionTotalTrials - Math.max(this.totalCount, 1)) + avgRt * Math.max(this.totalCount, 1)) / this.sessionTotalTrials
        : avgRt;

      this.currentRound++;

      if (roundAccuracy < 0.45) {
        // Failed round: hit rate too low
        this.state = 'finished';
        this._failRound(roundAccuracy);
      } else if (this.currentRound >= this.MAX_ROUNDS) {
        // All rounds done — end the session
        this.state = 'finished';
        this._endSession();
      } else {
        this.state = 'round_pause';
        this._passRound(roundAccuracy);
      }
      return;
    }

    this.currentTrial++;
    this.clickHandled = false;

    const W = this.canvas.width, H = this.canvas.height;
    const r = this.diff.target_radius;
    const margin = r + 10;

    // Target (primary blue, from COLORS["primary"] = (90, 120, 220))
    const tAngle = Math.random() * 2 * Math.PI;
    this.target = {
      x:  margin + Math.random() * (W - margin * 2),
      y:  margin + Math.random() * (H - margin * 2),
      vx: Math.cos(tAngle) * this.diff.target_speed,
      vy: Math.sin(tAngle) * this.diff.target_speed,
      radius: r,
      color: '#5a78dc',   // primary blue
      isDistractor: false,
    };

    // Distractors: n = floor(level * distractor_ratio * 2), gray color
    const nDistractors = Math.floor(this.diff.level * this.diff.distractor_ratio * 2);
    this.distractors = [];
    for (let i = 0; i < nDistractors; i++) {
      const dSpeedMult = 0.7 + Math.random() * 0.6;
      const dAngle     = Math.random() * 2 * Math.PI;
      this.distractors.push({
        x:  margin + Math.random() * (W - margin * 2),
        y:  margin + Math.random() * (H - margin * 2),
        vx: Math.cos(dAngle) * this.diff.target_speed * dSpeedMult,
        vy: Math.sin(dAngle) * this.diff.target_speed * dSpeedMult,
        radius: r,
        color: '#8c92a0',  // muted gray
        isDistractor: true,
      });
    }

    this.state      = 'target_shown';
    this.timer      = this.diff.stimulus_duration * 1000;  // ms
    this.stimAppeared = performance.now();
  }

  // ── Main loop ─────────────────────────────────────────────────────────────

  _loop(ts) {
    if (!this._running) return;
    if (this._paused) { this._raf = requestAnimationFrame(t => this._loop(t)); return; }
    const dt = ts - this._lastTs; this._lastTs = ts;
    this._update(dt);
    this._draw();
    this._raf = requestAnimationFrame(t => this._loop(t));
  }

  // ── State machine ─────────────────────────────────────────────────────────

  _update(dt) {
    const W = this.canvas.width, H = this.canvas.height;

    if (this.state === 'target_shown') {
      this.timer -= dt;

      // Move everything (dt is ms, speed is px/s)
      this._move(this.target, W, H, dt);
      this.distractors.forEach(d => this._move(d, W, H, dt));

      // Record trail position every ~60ms
      if (this.target) {
        this._trail.push({ x: this.target.x, y: this.target.y, alpha: 0.5 });
        if (this._trail.length > 4) this._trail.shift();
      }

      // Timeout → MISS
      if (this.timer <= 0 && !this.clickHandled) {
        this.clickHandled = true;
        this.totalCount++;
        this.lastHit = false;
        // Miss flash animation
        if (this.target) {
          this._missFlash = { x: this.target.x, y: this.target.y, alpha: 1, scale: 1 };
        }
        this.core.showFeedback(false);
        this.state = 'feedback';
        this.timer = this.diff.feedback_duration * 1000;
        const overallProgress = (this.currentRound + this.currentTrial / this.totalTrials) / this.MAX_ROUNDS;
        this.core._setProgress(overallProgress);
        this.core.setScore(this.sessionCorrect + this.correctCount);
      }
    } else if (this.state === 'feedback') {
      this.timer -= dt;
      // Decay miss flash
      if (this._missFlash) {
        this._missFlash.alpha -= dt * 0.003;
        this._missFlash.scale -= dt * 0.002;
        if (this._missFlash.alpha <= 0) this._missFlash = null;
      }
      if (this.timer <= 0) this._spawnNext();
    }
    // round_pause: game is idle, waiting for core.continueNextRound()
  }

  _move(obj, W, H, dt) {
    const s = dt / 1000;  // convert ms→s; speed is px/s
    obj.x += obj.vx * s;
    obj.y += obj.vy * s;
    const r = obj.radius;
    if (obj.x <= r)     { obj.x = r;     obj.vx =  Math.abs(obj.vx); }
    if (obj.x >= W - r) { obj.x = W - r; obj.vx = -Math.abs(obj.vx); }
    if (obj.y <= r)     { obj.y = r;     obj.vy =  Math.abs(obj.vy); }
    if (obj.y >= H - r) { obj.y = H - r; obj.vy = -Math.abs(obj.vy); }
  }

  // ── Input (mirrors: only target is clickable, distractors ignored) ─────────

  _handleClick(e) {
    if (this.state !== 'target_shown' || this.clickHandled) return;

    const rect = this.canvas.getBoundingClientRect();
    const px   = e.clientX - rect.left;
    const py   = e.clientY - rect.top;

    // Only check target — distractors are intentionally ignored per source
    if (!this.target) return;
    const dist = Math.hypot(px - this.target.x, py - this.target.y);
    if (dist <= this.target.radius) {
      // HIT
      this.clickHandled = true;
      const rt = Math.round(performance.now() - this.stimAppeared);
      this.reactionTimes.push(rt);
      this.correctCount++;
      this.totalCount++;
      this.lastHit = true;
      this.clickSpark = { x: this.target.x, y: this.target.y, alpha: 1, radius: 80 };
      this.core.showFeedback(true);
      this.state = 'feedback';
      this.timer = this.diff.feedback_duration * 1000;
      const overallProgress = (this.currentRound + this.currentTrial / this.totalTrials) / this.MAX_ROUNDS;
      this.core._setProgress(overallProgress);
      this.core.setScore(this.sessionCorrect + this.correctCount);
      this._trail = [];  // Clear trail on hit
    }
    // Misses near distractors or empty space are silently ignored (child can re-click)
  }

  // ── Render ───────────────────────────────────────────────────────────────

  _draw() {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;


    ctx.fillStyle = '#f5f7fa';
    ctx.fillRect(0, 0, W, H);

    // HUD bar — simplified with dot-based trial indicator
    ctx.fillStyle = '#ebedf2';
    ctx.fillRect(0, 0, W, 50);
    ctx.strokeStyle = '#dcdee6';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, 50); ctx.lineTo(W, 50); ctx.stroke();

    ctx.font = 'bold 20px Nunito, sans-serif';
    ctx.fillStyle = '#28283a';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(this.core.t("engine.roundCounter", { round: this.currentRound + 1, max: this.MAX_ROUNDS }), 30, 26);
    ctx.textAlign = 'right';
    ctx.fillStyle = '#5a78dc';
    ctx.fillText(`🎯 ${this.correctCount}`, W - 30, 26);

    // Trial dots (simplified progress)
    const maxDots = Math.min(this.totalTrials, 20);
    const dotGap = Math.min(14, (W - 120) / maxDots);
    const dotsStartX = (W - maxDots * dotGap) / 2;
    for (let i = 0; i < maxDots; i++) {
      ctx.beginPath();
      ctx.arc(dotsStartX + i * dotGap + dotGap / 2, 44, 3, 0, Math.PI * 2);
      ctx.fillStyle = i < this.currentTrial ? '#22c55e' : '#dde0ea';
      ctx.fill();
    }

    if (this.state === 'target_shown' || this.state === 'feedback') {
      // Draw motion trail
      this._trail.forEach((t, i) => {
        const alpha = (i + 1) / (this._trail.length + 1) * 0.25;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(t.x, t.y, this.diff.target_radius * 0.7, 0, Math.PI * 2);
        ctx.fillStyle = '#5a78dc';
        ctx.fill();
        ctx.restore();
      });

      // Draw distractors first (behind target)
      this.distractors.forEach(d => this._drawCircle(ctx, d));
      // Draw target on top
      if (this.target) this._drawCircle(ctx, this.target);
    } else if (this.state === 'finished') {
      ctx.font = 'bold 36px Nunito, sans-serif';
      ctx.fillStyle = '#5a78dc';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.core.t("engine.sessionComplete"), W / 2, H / 2);
    }

    // Click spark on hit — BIGGER (80px radius, slower fade)
    if (this.clickSpark) {
      const s = this.clickSpark;
      ctx.save();
      ctx.globalAlpha = s.alpha;
      const sparkR = s.radius || 80;
      const grd = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, sparkR);
      grd.addColorStop(0, '#5c78dc');
      grd.addColorStop(0.4, 'rgba(109,40,217,0.3)');
      grd.addColorStop(1, 'rgba(90,120,220,0)');
      ctx.beginPath(); ctx.arc(s.x, s.y, sparkR, 0, Math.PI * 2);
      ctx.fillStyle = grd; ctx.fill();
      ctx.restore();
      s.alpha -= 0.03;  // Slower fade
      if (s.alpha <= 0) this.clickSpark = null;
    }

    // Miss flash animation (red shrink)
    if (this._missFlash) {
      const m = this._missFlash;
      ctx.save();
      ctx.globalAlpha = Math.max(0, m.alpha);
      ctx.beginPath();
      ctx.arc(m.x, m.y, Math.max(0, this.diff.target_radius * m.scale), 0, Math.PI * 2);
      ctx.fillStyle = '#ef4444';
      ctx.fill();
      ctx.restore();
    }
  }

  _drawCircle(ctx, obj) {
    const { x, y, radius, color, isDistractor } = obj;

    // Pulsing glow ring for target at low levels (ADHD focus aid)
    if (!isDistractor && this.diff.level <= 3) {
      const glowPulse = Math.sin(Date.now() * 0.005) * 8 + 12;
      ctx.save();
      ctx.globalAlpha = 0.15 + Math.sin(Date.now() * 0.004) * 0.08;
      ctx.beginPath();
      ctx.arc(x, y, radius + glowPulse, 0, Math.PI * 2);
      ctx.strokeStyle = '#5a78dc'; ctx.lineWidth = 4; ctx.stroke();
      ctx.restore();
    }

    // Shadow (4px offset)
    ctx.save();
    ctx.globalAlpha = 0.18;
    ctx.beginPath(); ctx.arc(x + 4, y + 4, radius, 0, Math.PI * 2);
    ctx.fillStyle = '#000'; ctx.fill();
    ctx.restore();

    // Body
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();

    // White border
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = isDistractor ? '#c8ccd4' : '#fff';
    ctx.lineWidth   = 3; ctx.stroke();

    // Central dot for target (visual precision aid)
    if (!isDistractor) {
      ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#fff'; ctx.fill();
    }
  }

  _roundRect(ctx, x, y, w, h, r) { roundRect(ctx, x, y, w, h, r); }
  _lerpColor(c1, c2, t) { return lerpColorRGB(c1, c2, t); }

  // ── Round failed — show retry screen ──────────────────────────────────────

  _failRound(roundAccuracy) {
    // Record this failed round
    const avgRt = this.reactionTimes.length
      ? Math.round(this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length) : 0;
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound + 1, passed: false, accuracy: roundAccuracy,
      hits: this.correctCount, misses: this.totalCount - this.correctCount,
      avg_rt_ms: avgRt,
    });

    // Build complete session payload
    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const hits = this.sessionCorrect;
    const misses = Math.max(0, this.sessionTotal - this.sessionCorrect);
    const hitRate = hits / Math.max(this.sessionTotal, 1);
    const overallAvgRt = this.sessionRTs.length
      ? this.sessionRTs.reduce((a, b) => a + b, 0) / this.sessionRTs.length : 0;

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: this.sessionTotal,
      correct: hits,
      incorrect: misses,
      avgRT_s: overallAvgRt / 1000,
      completed: false,
      roundsPlayed: this.currentRound + 1,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: `failed_at_round_${this.currentRound + 1}`,
      roundDetails: this._roundDetails,
    });

    const payload = {
      ...base,
      hits, misses,
      hit_rate: Math.round(hitRate * 10000) / 10000,
      avg_rt_ms: Math.round(overallAvgRt * 100) / 100,
      rt_std_ms: 0,
      avg_target_speed: this.diff.target_speed,
      avg_distractor_count: 0,
      max_level_reached: this.diff.level,
    };

    const results = {
      gameName: 'tracking',
      accuracy: hitRate,
      level: this.diff.level,
      payload,
      displayMetrics: [
        { label: '🎯 Cibles touchées', value: this.correctCount },
        { label: '❌ Ratées',          value: this.totalCount - this.correctCount },
        { label: '📊 Précision',       value: `${Math.round(roundAccuracy * 100)} %` },
        { label: '💡 Conseil',         value: this.core.t("engine.trackingAdvice") },
      ],
    };
    this.diff.decrease();
    this.diff.save('tracking', this.player);
    setTimeout(() => this.core.onGameComplete(results), 1200);
  }

  _passRound(roundAccuracy) {
    const avgRt = this.reactionTimes.length
      ? Math.round(this.reactionTimes.reduce((a, b) => a + b, 0) / this.reactionTimes.length)
      : 0;
    // Record passed round
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound, passed: true, accuracy: roundAccuracy,
      hits: this.correctCount, misses: this.totalCount - this.correctCount,
      avg_rt_ms: avgRt,
    });
    this.core.onRoundPassed({
      round:    this.currentRound,
      maxRounds: this.MAX_ROUNDS,
      accuracy: roundAccuracy,
      level:    this.diff.level,
      displayMetrics: [
        { label: '🎯 Cibles touchées', value: this.correctCount },
        { label: '❌ Ratées',          value: this.totalCount - this.correctCount },
        { label: '📊 Précision',       value: `${Math.round(roundAccuracy * 100)} %` },
        { label: '⏱️ RT moyen',       value: avgRt ? `${avgRt} ms` : '—' },
      ],
    });
  }

  // ── Session end ──────────────────────────────────────────────────────────

  _endSession() {
    // Player completed all 3 rounds → always level up
    const completedLevel = this.diff.level;
    this.diff.increase();
    this.core.toast(this.core.t("engine.nextLevelToast"), '#50c878');

    // Explicitly save difficulty so level persists for next session
    this.diff.save('tracking', this.player);

    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const hits     = this.sessionCorrect;
    const misses   = Math.max(0, this.sessionTotal - this.sessionCorrect);
    const hitRate  = hits / Math.max(this.sessionTotal, 1);

    let avgRt = 0, rtStd = 0;
    if (this.sessionRTs.length > 0) {
      avgRt = this.sessionRTs.reduce((a, b) => a + b, 0) / this.sessionRTs.length;
      if (this.sessionRTs.length > 1) {
        rtStd = Math.sqrt(
          this.sessionRTs.reduce((a, b) => a + (b - avgRt) ** 2, 0) /
          (this.sessionRTs.length - 1)
        );
      }
    }

    const base = NeuroAPI.buildBase(this.player, {
      duration_s:   Math.round(duration * 10) / 10,
      totalActions: this.sessionTotal,
      correct:      hits,
      incorrect:    misses,
      avgRT_s:      avgRt / 1000,
      completed:    true,
      roundsPlayed: this.MAX_ROUNDS,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: 'completed',
      roundDetails: this._roundDetails,
      level: completedLevel,
    });

    const payload = {
      ...base,
      hits: hits,
      misses: misses,
      hit_rate:              Math.round(hitRate   * 10000) / 10000,
      avg_rt_ms:             Math.round(avgRt     * 100)   / 100,
      rt_std_ms:             Math.round(rtStd     * 100)   / 100,
      avg_target_speed:      Math.round(this.diff.target_speed * 100) / 100,
      avg_distractor_count:  Math.round(completedLevel * this.diff.distractor_ratio * 2 * 10) / 10,
      max_level_reached:     completedLevel,
    };

    const results = {
      gameName: 'tracking',
      accuracy: hitRate,
      level:    completedLevel,
      payload,
      displayMetrics: [
        { label: '🎯 Hits',         value: hits },
        { label: '❌ Ratés',        value: misses },
        { label: '📊 Précision',    value: `${Math.round(hitRate * 100)} %` },
        { label: '⏱ RT moyen',     value: avgRt ? `${Math.round(avgRt)} ms` : '—' },
        { label: '⚡ Variabilité',  value: rtStd ? `${Math.round(rtStd)} ms` : '—' },
        { label: '🎯 Niveau',           value: completedLevel },
      ],
    };

    setTimeout(() => this.core.onGameComplete(results), 1200);
  }

  getPartialResults() {
    if (this.state === 'finished' || this.sessionTotal === 0) return null;

    const duration = (Date.now() - this.sessionStartMs) / 1000;
    const hits     = this.sessionCorrect;
    const misses   = Math.max(0, this.sessionTotal - this.sessionCorrect);
    const hitRate  = hits / Math.max(this.sessionTotal, 1);

    let avgRt = 0, rtStd = 0;
    if (this.sessionRTs.length > 0) {
      avgRt = this.sessionRTs.reduce((a, b) => a + b, 0) / this.sessionRTs.length;
      if (this.sessionRTs.length > 1) {
        rtStd = Math.sqrt(
          this.sessionRTs.reduce((a, b) => a + (b - avgRt) ** 2, 0) /
          (this.sessionRTs.length - 1)
        );
      }
    }

    const base = NeuroAPI.buildBase(this.player, {
      duration_s:   Math.round(duration * 10) / 10,
      totalActions: this.sessionTotal,
      correct:      hits,
      incorrect:    misses,
      avgRT_s:      avgRt / 1000,
      completed:    false,
      roundsPlayed: this.currentRound,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: 'interrupted',
      roundDetails: this._roundDetails,
    });

    const payload = {
      ...base,
      hits: hits,
      misses: misses,
      hit_rate:              Math.round(hitRate   * 10000) / 10000,
      avg_rt_ms:             Math.round(avgRt     * 100)   / 100,
      rt_std_ms:             Math.round(rtStd     * 100)   / 100,
      avg_target_speed:      Math.round(this.diff.target_speed * 100) / 100,
      avg_distractor_count:  Math.round(this.diff.level * this.diff.distractor_ratio * 2 * 10) / 10,
      max_level_reached:     this.diff.level,
    };

    return {
      gameName: 'tracking',
      accuracy: hitRate,
      level:    this.diff.level,
      payload,
      displayMetrics: [],
    };
  }
}
