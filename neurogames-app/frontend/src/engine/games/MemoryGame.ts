// @ts-nocheck
import { DifficultyProfile } from '../DifficultyProfile';
import { NeuroAPI } from '../neuroApi';
import { roundRect, lerpColorHex } from '../utils';

/**
 * MemoryGame.js — Memory Match (faithful web port of memory/memory_game.py)
 *
 * State machine: preview → playing → hide_pause → (loop) → finished
 *
 * Grid from DifficultyProfile (mirrors source):
 *   Default: grid_rows=2, grid_cols=3   (6 cards)
 *   Level 4: grid_cols=4                (12 cards)
 *   Level 7: grid_cols=4, grid_rows=4   (16 cards)
 *   card_preview_duration = 2.0s (decreases with level)
 *
 * Card symbols (from source CARD_SYMBOLS): circle, square, triangle, star, cross, diamond
 * Pair colours (from source PAIR_COLORS): green, blue, orange, red, purple, cyan, yellow, pink
 *
 * Face-down card: dark blue-purple (#465aaa) + diamond pattern + '?' (mirrors source)
 * Hint (level ≤ 3): coloured border on face-down cards (source: colored border at level <= 3)
 * Face-up / matched display: coloured border, geometric shape drawn on canvas
 * hide_pause = 1.0s (fixed, from source: self._hide_timer.start(1.0))
 *
 * Metrics (MemorySession schema):
 *   pairs_found, total_pairs, total_attempts, match_accuracy, completion_time_s
 */

// Exact PAIR_COLORS from source (converted to CSS hex)
const PAIR_COLORS_MEM = [
  '#50c878',  // (80, 200, 120) green
  '#5a78dc',  // (90, 120, 220) blue
  '#ffaa50',  // (255, 170, 80) orange
  '#e65a5a',  // (230, 90, 90) red
  '#b482f0',  // (180, 130, 240) purple
  '#3cc8c8',  // (60, 200, 200) cyan
  '#ffc83c',  // (255, 200, 60) yellow
  '#c86496',  // (200, 100, 150) pink
];

// Exact CARD_SYMBOLS from source
const CARD_SYMBOLS_MEM = ['circle', 'square', 'triangle', 'star', 'cross', 'diamond'];

export class MemoryGame {
  constructor(canvas, player, core) {
    this.canvas = canvas;
    this.ctx    = canvas.getContext('2d');
    this.player = player;
    this.core   = core;

    // ── DifficultyProfile (mirrored from Python, starting level) ──────────
    this.diff = DifficultyProfile.load('memory', player);
    
    this.MAX_ROUNDS = 3;
    this.currentRound = 0;

    // ── Session-wide stats for end-of-session adaptation ─────────────────
    this.sessionTotalCorrect = 0;
    this.sessionTotalTrials  = 0;
    this.sessionAvgRtMs      = 0;

    // ── Session Cumulative ────────────────────────────────────────────────
    this.sessionAttempts = 0;
    this.sessionPairsFound = 0;
    this.sessionTotalPairs = 0;
    
    // ── Grid ──────────────────────────────────────────────────────────────
    this.gridRows   = this.diff.grid_rows;
    this.gridCols   = this.diff.grid_cols;
    this.cards      = [];
    this.cardRects  = {};   // id → { x, y, w, h }
    this.hintColors = {};   // symbol → color (easy mode)

    // ── State ─────────────────────────────────────────────────────────────
    this.flipped    = [];   // at most 2 cards
    this.attempts   = 0;
    this.pairsFound = 0;
    this.totalPairs = 0;

    this.state      = 'preview';
    this.timer      = 0;     // countdown ms (preview or hide_pause)
    this.startMs    = 0;
    this.finishMs   = 0;

    // ── Animation ─────────────────────────────────────────────────────────
    this.matchFlash = {};   // card.id → alpha

    // ── Loop ──────────────────────────────────────────────────────────────
    this._raf    = null;
    this._lastTs = null;
    this._running = false;
    this._paused = false;

    // Shake animation state for mismatch
    this._shakeTimer = 0;
    this._shakeCards = [];

    // Consecutive match streak
    this._matchStreak = 0;
    this._streakDisplay = 0;
    this._streakFade = 0;
    this._roundDetails = [];

    this._onPointer = this._handleClick.bind(this);
  }

  // Difficulty profile handled by external adapter
  // ── Lifecycle ─────────────────────────────────────────────────────────────

  start() {
    this.currentRound = 0;
    this.sessionAttempts = 0;
    this.sessionPairsFound = 0;
    this.sessionTotalPairs = 0;
    this.sessionTotalCorrect = 0;
    this.sessionTotalTrials  = 0;
    this.sessionAvgRtMs      = 0;
    this.startMs = Date.now();
    this._running = true;

    // Show the persisted level immediately on the HUD
    this.core.setLevel(this.diff.level);

    this.canvas.addEventListener('pointerdown', this._onPointer);
    
    this._startRound();

    this._lastTs = performance.now();
    this._loop(this._lastTs);
  }

  _startRound() {
    this.gridRows   = this.diff.grid_rows;
    this.gridCols   = this.diff.grid_cols;
    this.totalPairs = (this.gridRows * this.gridCols) / 2;

    this._generateCards();
    this._computeCardRects();

    this.flipped    = [];
    this.attempts   = 0;
    this.pairsFound = 0;

    // All face-up during preview
    this.cards.forEach(c => { c.flipped = true; });

    this.state   = 'preview';
    this.timer   = this.diff.card_preview_duration * 1000;
    this.roundStartMs = Date.now();

    this.core.setLevel(this.diff.level);
    const overallProgress = this.currentRound / this.MAX_ROUNDS;
    this.core._setProgress(overallProgress);
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.canvas.removeEventListener('pointerdown', this._onPointer);
  }

  pause() { this._paused = true; }
  resume() { this._paused = false; this._lastTs = performance.now(); }

  onResize() { this._computeCardRects(); }

  // ── Card generation (mirrors _generate_cards) ─────────────────────────────

  _generateCards() {
    const nCards  = this.gridRows * this.gridCols;
    const nPairs  = nCards / 2;

    const symbols = [], colors = [];
    this.hintColors = {};
    for (let i = 0; i < nPairs; i++) {
      symbols.push(CARD_SYMBOLS_MEM[i % CARD_SYMBOLS_MEM.length]);
      colors.push(PAIR_COLORS_MEM[i % PAIR_COLORS_MEM.length]);
    }
    // hint colors for easy mode
    for (let i = 0; i < nPairs; i++) this.hintColors[symbols[i]] = colors[i];

    // Build pairs
    const data = [];
    for (let i = 0; i < nPairs; i++) { data.push([symbols[i], colors[i]]); data.push([symbols[i], colors[i]]); }
    // Shuffle
    for (let i = data.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [data[i], data[j]] = [data[j], data[i]];
    }

    this.cards = [];
    let idx = 0;
    for (let r = 0; r < this.gridRows; r++) {
      for (let c = 0; c < this.gridCols; c++) {
        this.cards.push({ id: idx, symbol: data[idx][0], color: data[idx][1], row: r, col: c, flipped: false, matched: false });
        idx++;
      }
    }
  }

  // ── Layout (mirrors _compute_card_rects) ─────────────────────────────────

  _computeCardRects() {
    const W = this.canvas.width, H = this.canvas.height;
    const maxCardW = 140, maxCardH = 160, spacing = 16;
    const cardW = Math.min(maxCardW, Math.floor((W - 40 - spacing * (this.gridCols - 1)) / this.gridCols));
    const cardH = Math.min(maxCardH, Math.floor((H - 80 - spacing * (this.gridRows - 1)) / this.gridRows));
    const totalW = this.gridCols * cardW + (this.gridCols - 1) * spacing;
    const totalH = this.gridRows * cardH + (this.gridRows - 1) * spacing;
    const startX = Math.floor((W - totalW) / 2);
    const startY = Math.floor((H - totalH) / 2) + 30;

    this.cardRects = {};
    this.cardW = cardW; this.cardH = cardH;
    this.cards.forEach(card => {
      this.cardRects[card.id] = {
        x: startX + card.col * (cardW + spacing),
        y: startY + card.row * (cardH + spacing),
        w: cardW, h: cardH,
      };
    });
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

  // ── State machine (mirrors update_game) ───────────────────────────────────

  _update(dt) {
    this.timer -= dt;

    if (this.state === 'preview') {
      if (this.timer <= 0) {
        // Flip all face-down (mirrors: card.flipped = False for all)
        this.cards.forEach(c => { c.flipped = false; });
        this.state = 'playing';
      }
    } else if (this.state === 'hide_pause') {
      // 1.0s pause after mismatch (mirrors: self._hide_timer.start(1.0))
      if (this.timer <= 0) {
        this.flipped.forEach(c => { c.flipped = false; });
        this.flipped = [];
        this.state = 'playing';
      }
    }
    // round_pause: game is idle, waiting for core.continueNextRound()

    // Decay match flash
    Object.keys(this.matchFlash).forEach(id => {
      this.matchFlash[id] = Math.max(0, this.matchFlash[id] - 0.04);
      if (this.matchFlash[id] <= 0) delete this.matchFlash[id];
    });

    // Shake timer decay
    if (this._shakeTimer > 0) this._shakeTimer -= dt;

    // Streak fade
    if (this._streakFade > 0) this._streakFade = Math.max(0, this._streakFade - dt * 0.001);
  }

  // ── Click handler (mirrors _update_playing) ───────────────────────────────

  _handleClick(e) {
    if (this.state !== 'playing') return;
    if (this.flipped.length >= 2) return;  // mirrors: if len(self.flipped) >= 2: return

    const rect = this.canvas.getBoundingClientRect();
    const px   = e.clientX - rect.left;
    const py   = e.clientY - rect.top;

    const card = this.cards.find(c => {
      if (c.flipped || c.matched) return false;  // mirrors: if clicked_card.flipped or clicked_card.matched: return
      const r = this.cardRects[c.id];
      return r && px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h;
    });
    if (!card) return;

    card.flipped = true;
    this.flipped.push(card);

    if (this.flipped.length === 2) {
      this.attempts++;
      this._checkMatch();
    }
  }

  // ── Match check (mirrors _check_match) ────────────────────────────────────

  _checkMatch() {
    const [c1, c2] = this.flipped;
    if (c1.symbol === c2.symbol) {
      // Match found!
      c1.matched = true; c2.matched = true;
      this.pairsFound++;
      this.matchFlash[c1.id] = 1; this.matchFlash[c2.id] = 1;
      this.core.showFeedback(true);
      this.flipped = [];  // mirrors: self.flipped = []

      // Streak counter
      this._matchStreak++;
      if (this._matchStreak >= 2) {
        this._streakDisplay = this._matchStreak;
        this._streakFade = 1.0;
        if (this._matchStreak >= 3) this.core.playSound('combo');
      }

      this.sessionPairsFound++;
      const overallProgress = (this.currentRound + this.pairsFound / this.totalPairs) / this.MAX_ROUNDS;
      this.core._setProgress(overallProgress);
      this.core.setScore(this.sessionPairsFound);

      if (this.pairsFound >= this.totalPairs) {
        this.currentRound++;
        this.sessionAttempts += this.attempts;
        this.sessionTotalPairs += this.totalPairs;

        // Round accuracy: pairs_found / attempts (perfect = 1.0)
        const roundAccuracy = Math.min(1.0, this.totalPairs / Math.max(this.attempts, 1));
        
        // Accumulate stats for end-of-session adaptation (avgRt = time per attempt)
        const avgRt = (Date.now() - this.roundStartMs) / Math.max(1, this.attempts);
        this.sessionTotalCorrect += this.pairsFound;
        this.sessionTotalTrials  += Math.max(1, this.attempts);
        this.sessionAvgRtMs = this.sessionTotalTrials > 0
          ? (this.sessionAvgRtMs * (this.sessionTotalTrials - Math.max(1, this.attempts)) + avgRt * Math.max(1, this.attempts)) / this.sessionTotalTrials
          : avgRt;

        if (roundAccuracy < 0.45) {
          // Failed round: too many attempts needed
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
      }
    } else {
      // Mismatch: pause 1.0s then flip back (mirrors: self._hide_timer.start(1.0))
      this.core.showFeedback(false);
      this.state = 'hide_pause';
      this.timer = 1000;  // 1.0s
      this._shakeTimer = 400;  // 400ms shake
      this._shakeCards = [c1.id, c2.id];
      this._matchStreak = 0;  // Reset streak on mismatch
    }
  }

  // ── Render (mirrors draw_game + _draw_card) ───────────────────────────────

  _draw() {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;


    // Background (mirrors COLORS["bg"] = (245, 247, 250))
    ctx.fillStyle = '#f5f7fa';
    ctx.fillRect(0, 0, W, H);

    // HUD (mirrors _draw_hud)
    this._drawHUD(ctx, W);

    // Cards
    this.cards.forEach(c => {
      const r = this.cardRects[c.id];
      if (r) {
        // Apply shake offset for mismatched cards
        let shakeOff = 0;
        if (this._shakeTimer > 0 && this._shakeCards.includes(c.id)) {
          shakeOff = Math.sin(this._shakeTimer * 0.05) * 5;
        }
        const drawR = shakeOff ? { ...r, x: r.x + shakeOff } : r;
        this._drawCard(ctx, c, drawR);
      }
    });

    // Preview indicator with countdown ring
    if (this.state === 'preview') {
      const secs = Math.max(0, this.timer / 1000);
      const total = this.diff.card_preview_duration;
      const pct = secs / total;
      // Countdown ring
      const ringX = W / 2, ringY = 28, ringR = 14;
      ctx.save();
      ctx.beginPath();
      ctx.arc(ringX - 200, ringY, ringR, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(90,120,220,0.2)'; ctx.lineWidth = 4; ctx.stroke();
      ctx.beginPath();
      ctx.arc(ringX - 200, ringY, ringR, -Math.PI / 2, -Math.PI / 2 + pct * Math.PI * 2);
      ctx.strokeStyle = '#5a78dc'; ctx.lineWidth = 4; ctx.stroke();

      ctx.font = 'bold 24px Nunito, sans-serif';
      ctx.fillStyle = '#5a78dc';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.core.t("engine.memorizeCards", { seconds: secs.toFixed(1) }), W / 2, ringY);
      ctx.restore();
    }

    // Streak counter display
    if (this._streakFade > 0 && this._streakDisplay >= 2) {
      ctx.save();
      ctx.globalAlpha = this._streakFade;
      ctx.font = 'bold 28px Nunito, sans-serif';
      ctx.fillStyle = '#FCD34D';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(`🔥 ×${this._streakDisplay}`, W / 2, 76);
      ctx.restore();
    }

    // Finished overlay (mirrors _draw_finished)
    if (this.state === 'finished') {
      ctx.save();
      ctx.fillStyle = 'rgba(245,247,250,0.85)';
      ctx.fillRect(0, 0, W, H);
      ctx.font = 'bold 28px Nunito, sans-serif';
      ctx.fillStyle = '#50c878';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.core.t("engine.bravo"), W / 2, H / 2);
      ctx.font = '22px Nunito, sans-serif';
      ctx.fillStyle = '#8c92a0';
      ctx.fillText(this.core.t("engine.sessionComplete"), W / 2, H / 2 + 44);
      ctx.restore();
    }

    // Progress bar (mirrors _draw_progress_bar)
    const bh = 8, bx = 40, by = H - bh - 10, bw = W - 80;
    ctx.fillStyle = '#ebedf2';
    this._roundRect(ctx, bx, by, bw, bh, 4); ctx.fill();
    const pct = this.pairsFound / Math.max(this.totalPairs, 1);
    if (pct > 0) {
      const c = this._lerp('#5a78dc', '#50c878', pct);
      ctx.fillStyle = c;
      this._roundRect(ctx, bx, by, bw * pct, bh, 4); ctx.fill();
    }
  }

  _drawHUD(ctx, W) {
    ctx.save();
    ctx.fillStyle = 'rgba(30,35,50,0.06)';
    ctx.fillRect(0, 0, W, 55);

    // Left: Round dots + round label
    ctx.font = 'bold 18px Nunito, sans-serif';
    ctx.textBaseline = 'middle';
    const dotX = 20;
    for (let i = 0; i < this.MAX_ROUNDS; i++) {
      ctx.beginPath(); ctx.arc(dotX + i * 18, 22, 5, 0, Math.PI * 2);
      ctx.fillStyle = i < this.currentRound ? '#50c878' : (i === this.currentRound ? '#5a78dc' : '#dde0ea');
      ctx.fill();
    }
    ctx.fillStyle = '#8c92a0'; ctx.textAlign = 'left';
    ctx.fillText(this.core.t("engine.roundCounter", { round: this.currentRound + 1, max: this.MAX_ROUNDS }), dotX, 42);

    // Counter and Level on the right
    ctx.font = 'bold 18px Nunito, sans-serif';
    ctx.fillStyle = '#28283a'; ctx.textAlign = 'right';
    ctx.fillText(`🃏 ${this.pairsFound} / ${this.totalPairs}`, W - 100, 28);

    // Right: level
    ctx.font = 'bold 18px Nunito, sans-serif';
    ctx.fillStyle = '#5a78dc'; ctx.textAlign = 'right';
    ctx.fillText(`Nv ${this.diff.level}`, W - 20, 28);

    ctx.restore();
  }

  // _draw_card dispatcher
  _drawCard(ctx, card, r) {
    if (card.matched)      this._drawMatchedCard(ctx, card, r);
    else if (card.flipped) this._drawFaceUpCard(ctx, card, r);
    else                   this._drawFaceDownCard(ctx, card, r);

    // Match flash glow
    if (this.matchFlash[card.id]) {
      const a = this.matchFlash[card.id];
      ctx.save(); ctx.globalAlpha = a * 0.5;
      ctx.fillStyle = '#50c878';
      this._cardRR(ctx, r); ctx.fill();
      ctx.restore();
    }
  }

  // _draw_face_up_card
  _drawFaceUpCard(ctx, card, r) {
    // Shadow
    ctx.save(); ctx.globalAlpha = 0.18;
    this._cardRR(ctx, { x: r.x+3, y: r.y+3, w: r.w, h: r.h }); ctx.fillStyle = '#000'; ctx.fill();
    ctx.restore();
    // White bg
    this._cardRR(ctx, r); ctx.fillStyle = '#fff'; ctx.fill();
    // Coloured border (mirrors pygame.draw.rect(screen, card.color, rect, 3, border_radius=12))
    this._cardRR(ctx, r); ctx.strokeStyle = card.color; ctx.lineWidth = 3; ctx.stroke();
    // Symbol
    const symbolSize = Math.min(r.w, r.h) - 40;
    this._drawSymbol(ctx, card.symbol, r.x + r.w / 2, r.y + r.h / 2, symbolSize / 2, card.color, true);
  }

  // _draw_face_down_card
  _drawFaceDownCard(ctx, card, r) {
    // Shadow
    ctx.save(); ctx.globalAlpha = 0.18;
    this._cardRR(ctx, { x: r.x+3, y: r.y+3, w: r.w, h: r.h }); ctx.fillStyle = '#000'; ctx.fill();
    ctx.restore();
    // Back colour: back_color = (70, 90, 170)  → #465aaa
    this._cardRR(ctx, r); ctx.fillStyle = '#465aaa'; ctx.fill();
    // Diamond pattern (mirrors source diamond_size=18)
    const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
    const ds = 18;
    ctx.beginPath();
    ctx.moveTo(cx, cy - ds); ctx.lineTo(cx + ds, cy);
    ctx.lineTo(cx, cy + ds); ctx.lineTo(cx - ds, cy);
    ctx.closePath();
    ctx.fillStyle = '#6478c8'; ctx.fill();
    ctx.strokeStyle = '#8296e6'; ctx.lineWidth = 2; ctx.stroke();
    // ? below diamond (mirrors font_card.render "?" at center + 35)
    ctx.font = 'bold 22px Nunito, sans-serif';
    ctx.fillStyle = '#b4bee0'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText('?', cx, cy + 35);

    // Coloured border hint (mirrors: if self.difficulty.level <= 3 and card.symbol in self._hint_colors)
    if (this.diff.level <= 3 && this.hintColors[card.symbol]) {
      ctx.save();
      this._cardRR(ctx, r);
      ctx.strokeStyle = this.hintColors[card.symbol] + 'aa';   // ~alpha 120/255
      ctx.lineWidth = 4; ctx.stroke();
      ctx.restore();
    }
  }

  // _draw_matched_card
  _drawMatchedCard(ctx, card, r) {
    // Semi-transparent light green (mirrors (240, 255, 240, 200))
    ctx.save(); ctx.globalAlpha = 0.78;
    this._cardRR(ctx, r); ctx.fillStyle = '#f0fff0'; ctx.fill();
    ctx.restore();
    // Green border
    this._cardRR(ctx, r); ctx.strokeStyle = '#50c878'; ctx.lineWidth = 3; ctx.stroke();
    // Faded symbol (mirrors faded_color = tuple(min(255, c + 60) for c in card.color))
    const symbolSize = Math.min(r.w, r.h) - 40;
    this._drawSymbol(ctx, card.symbol, r.x + r.w / 2, r.y + r.h / 2, symbolSize / 2, card.color, true, 0.45);
    // Checkmark top-right (mirrors check_surf.get_rect(topright=(rect.right-8, rect.top+5)))
    ctx.font = 'bold 16px Nunito, sans-serif';
    ctx.fillStyle = '#50c878'; ctx.textAlign = 'right'; ctx.textBaseline = 'top';
    ctx.fillText('✓', r.x + r.w - 8, r.y + 5);
  }

  // ── Symbol renderer (mirrors StimulusRenderer.draw + _draw_shape in source) ─

  /**
   * @param {string} sym     circle|square|triangle|star|cross|diamond
   * @param {number} cx/cy   center
   * @param {number} s       half-size radius
   * @param {boolean} fill   fill vs stroke
   * @param {number} alpha   optional alpha for matched cards
   */
  _drawSymbol(ctx, sym, cx, cy, s, color, fill = true, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle   = color;
    ctx.strokeStyle = color;
    ctx.lineWidth   = 3;

    ctx.beginPath();
    switch (sym) {
      case 'circle':
        ctx.arc(cx, cy, s, 0, Math.PI * 2);
        break;
      case 'square':
        ctx.rect(cx - s, cy - s, s * 2, s * 2);
        break;
      case 'triangle':
        ctx.moveTo(cx, cy - s);
        ctx.lineTo(cx - s, cy + s);
        ctx.lineTo(cx + s, cy + s);
        ctx.closePath();
        break;
      case 'star': {
        for (let i = 0; i < 10; i++) {
          const angle = (i * 36 * Math.PI) / 180;
          const r2    = i % 2 === 0 ? s : s * 0.45;
          const px    = cx + r2 * Math.sin(angle);
          const py    = cy - r2 * Math.cos(angle);
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
        break;
      }
      case 'cross':
        ctx.rect(cx - s * 0.28, cy - s, s * 0.56, s * 2);
        ctx.rect(cx - s, cy - s * 0.28, s * 2, s * 0.56);
        break;
      case 'diamond':
        ctx.moveTo(cx, cy - s); ctx.lineTo(cx + s * 0.7, cy);
        ctx.lineTo(cx, cy + s); ctx.lineTo(cx - s * 0.7, cy);
        ctx.closePath();
        break;
      default:
        ctx.arc(cx, cy, s, 0, Math.PI * 2);
    }

    if (fill) ctx.fill(); else ctx.stroke();
    ctx.restore();
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  _cardRR(ctx, r) { roundRect(ctx, r.x, r.y, r.w, r.h, 12); }
  _roundRect(ctx, x, y, w, h, r) { roundRect(ctx, x, y, w, h, r); }
  _lerp(hex1, hex2, t) { return lerpColorHex(hex1, hex2, t); }

  // ── Round failed — show retry screen ──────────────────────────────────────

  _failRound(roundAccuracy) {
    // Record this failed round
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound + 1, passed: false, accuracy: roundAccuracy,
      pairs_found: this.pairsFound, total_pairs: this.totalPairs,
      attempts: this.attempts,
    });

    // Build complete session payload
    const duration = (Date.now() - this.startMs) / 1000;
    const overallAccuracy = this.sessionPairsFound / Math.max(this.sessionAttempts, 1);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: this.sessionAttempts,
      correct: this.sessionPairsFound,
      incorrect: this.sessionAttempts - this.sessionPairsFound,
      completed: false,
      roundsPlayed: this.currentRound + 1,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: `failed_at_round_${this.currentRound + 1}`,
      roundDetails: this._roundDetails,
    });

    const payload = {
      ...base,
      pairs_found: this.sessionPairsFound,
      total_pairs: this.sessionTotalPairs,
      total_attempts: this.sessionAttempts,
      match_accuracy: Math.round(overallAccuracy * 10000) / 10000,
      completion_time_s: Math.round(duration * 100) / 100,
      max_level_reached: this.diff.level,
    };

    const results = {
      gameName: 'memory',
      accuracy: overallAccuracy,
      level: this.diff.level,
      payload,
      displayMetrics: [
        { label: '🃏 Paires trouvées',  value: `${this.pairsFound} / ${this.totalPairs}` },
        { label: '🔄 Essais',           value: this.attempts },
        { label: '📊 Précision',        value: `${Math.round(roundAccuracy * 100)} %` },
        { label: '💡 Conseil',          value: this.core.t("engine.memoryAdvice") },
      ],
    };
    // Decrease difficulty on failure + save
    this.diff.decrease();
    this.diff.save('memory', this.player);
    setTimeout(() => this.core.onGameComplete(results), 1000);
  }

  _passRound(roundAccuracy) {
    // Record passed round
    this._roundDetails.push({
      level: this.diff.level, round: this.currentRound, passed: true, accuracy: roundAccuracy,
      pairs_found: this.pairsFound, total_pairs: this.totalPairs,
      attempts: this.attempts,
    });
    this.core.onRoundPassed({
      round:    this.currentRound,
      maxRounds: this.MAX_ROUNDS,
      accuracy: roundAccuracy,
      level:    this.diff.level,
      displayMetrics: [
        { label: '🃏 Paires trouvées',  value: `${this.pairsFound} / ${this.totalPairs}` },
        { label: '🔄 Essais',           value: this.attempts },
        { label: '📊 Précision',        value: `${Math.round(roundAccuracy * 100)} %` },
      ],
    });
  }

  // ── Session end ────────────────────────────────────────────────────────────

  _endSession() {
    // Player completed all 3 rounds → always level up
    // (each round already required ≥45% accuracy to pass)
    const completedLevel = this.diff.level;
    this.diff.increase();
    this.core.toast(this.core.t("engine.nextLevelToast"), '#50c878');

    // Explicitly save difficulty so level persists for next session
    this.diff.save('memory', this.player);

    const duration = (Date.now() - this.startMs) / 1000;
    const accuracy = this.sessionPairsFound / Math.max(this.sessionAttempts, 1);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s:   Math.round(duration * 10) / 10,
      totalActions: this.sessionAttempts,
      correct:      this.sessionPairsFound,
      incorrect:    this.sessionAttempts - this.sessionPairsFound,
      completed:    true,
      roundsPlayed: this.MAX_ROUNDS,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: 'completed',
      roundDetails: this._roundDetails,
      level: completedLevel,
    });

    const payload = {
      ...base,
      pairs_found:       this.sessionPairsFound,
      total_pairs:       this.sessionTotalPairs,
      total_attempts:    this.sessionAttempts,
      match_accuracy:    Math.round(accuracy * 10000) / 10000,
      completion_time_s: Math.round(duration * 100) / 100,
      max_level_reached: completedLevel,
    };

    const results = {
      gameName: 'memory',
      accuracy,
      level:    completedLevel,
      payload,
      displayMetrics: [
        { label: '🃏 Paires trouvées', value: `${this.sessionPairsFound} / ${this.sessionTotalPairs}` },
        { label: '🔄 Essais totaux',   value: this.sessionAttempts },
        { label: '🎯 Précision global', value: `${Math.round(accuracy * 100)} %` },
        { label: '⏱️ Temps',           value: `${duration.toFixed(1)} s` },
        { label: '🎯 Niveau',           value: completedLevel },
      ],
    };

    this.core.onGameComplete(results);
  }

  getPartialResults() {
    if (this.state === 'finished' || this.sessionAttempts === 0) return null;
    const duration = (Date.now() - this.startMs) / 1000;
    const accuracy = this.sessionPairsFound / Math.max(this.sessionAttempts, 1);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s:   Math.round(duration * 10) / 10,
      totalActions: this.sessionAttempts,
      correct:      this.sessionPairsFound,
      incorrect:    this.sessionAttempts - this.sessionPairsFound,
      completed:    false,
      roundsPlayed: this.currentRound,
      roundsPassed: this._roundDetails.filter(r => r.passed).length,
      sessionOutcome: 'interrupted',
      roundDetails: this._roundDetails,
      level: this.diff.level,
    });

    const payload = {
      ...base,
      pairs_found:       this.sessionPairsFound,
      total_pairs:       this.sessionTotalPairs,
      total_attempts:    this.sessionAttempts,
      match_accuracy:    Math.round(accuracy * 10000) / 10000,
      completion_time_s: Math.round(duration * 100) / 100,
      max_level_reached: this.diff.level,
    };

    return {
      gameName: 'memory',
      accuracy,
      level: this.diff.level,
      payload,
      displayMetrics: [],
    };
  }
}
