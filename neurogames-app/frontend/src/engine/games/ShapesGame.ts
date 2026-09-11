// @ts-nocheck
import { NeuroAPI } from '../neuroApi';
import { roundRect } from '../utils';

/**
 * ShapesGame.js — Sliding Puzzle (faithful web port of shapes/shapes_game.py)
 *
 * A SLIDING PUZZLE — one empty cell, click adjacent tiles to swap to the empty slot.
 * Goal: sort each column so it contains only the expected shape.
 *
 * Level progression: cycles through levels inside the game session.
 * Session ends after MAX_SESSION_LEVELS (5) levels or when the player gives up.
 * onGameComplete is called once at the end of the whole session.
 *
 * Grid size (from source): levels 1–3 → 2×2, 4–6 → 3×3, 7–9 → 4×4
 * max_moves = A*_optimal + move_modifier; cycle: +12, +8, +4
 * Victory: same column = same shape. Defeat: moves >= max_moves and not solved.
 */

const SHAPE_DATA = [
  { name: 'carre',    color: '#ff3232' },
  { name: 'triangle', color: '#3232ff' },
  { name: 'cercle',   color: '#32c832' },
  { name: 'etoile',   color: '#ffc800' },
];

const MAX_SESSION_LEVELS = 5;  // how many levels before session ends

export class ShapesGame {
  constructor(canvas, player, core) {
    this.canvas = canvas;
    this.ctx    = canvas.getContext('2d');
    this.player = player;
    this.core   = core;

    this.level = 1;

    // Puzzle state
    this.grid        = [];
    this.gridSize    = 2;
    this.cellSize    = 110;
    this.gridOriginX = 0;
    this.gridOriginY = 0;

    this.movesCount   = 0;
    this.maxMoves     = 20;
    this.optimalMoves = 10;
    this.startMs      = 0;
    this.elapsedMs    = 0;

    this.victory  = false;
    this.lost     = false;
    this.endTime  = 0;  // performance.now() at win/lose

    // Session tracking (for the full session)
    this.sessStartMs    = 0;
    this.levelsPlayed   = 0;   // level attempts completed, failed, or redone
    this.levelsWon      = 0;
    this.levelsFailed   = 0;
    this.redoCount      = 0;
    this.totalMoves     = 0;
    this.sessionLevel   = 1;  // always start from level 1
    this.levelMetrics   = [];
    this.levelOutcomes  = [];

    this._raf     = null;
    this._lastTs  = null;
    this._running = false;
    this._paused  = false;
    this._sessionEnded = false;
    this._onClick = this._handleClick.bind(this);
    this._lastMove = null;  // { fromR, fromC, toR, toC } for undo
    this._attemptRecorded = false;
  }
  // ── Lifecycle ─────────────────────────────────────────────────────────────
  start() {
    this.sessStartMs  = Date.now();
    this.level        = this.sessionLevel;
    this.levelsPlayed = 0;
    this.levelsWon    = 0;
    this.levelsFailed = 0;
    this.redoCount    = 0;
    this.totalMoves   = 0;
    this.levelMetrics = [];
    this.levelOutcomes = [];
    this._sessionEnded = false;
    this._running = true;
    this.canvas.addEventListener('pointerdown', this._onClick);
    this._initLevel(this.level);
    this._lastTs = performance.now();
    this._loop(this._lastTs);
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.canvas.removeEventListener('pointerdown', this._onClick);
  }

  onResize() { this._computeLayout(); }

  pause() { this._paused = true; }
  resume() { this._paused = false; this._lastTs = performance.now(); }

  // ── Level init (mirrors _init_level) ──────────────────────────────────────
  _initLevel(level) {
    this.level    = level;
    this.gridSize = Math.floor((level - 1) / 3) + 2;   // 1-3→2, 4-6→3, 7-9→4

    const moveModifiers = [12, 8, 4];
    const moveMod = moveModifiers[(level - 1) % 3];
    this.cellSize = this.gridSize < 4 ? 110 : 80;

    this._buildTokens();
    this._computeLayout();

    this.victory   = false;
    this.lost      = false;
    this.endTime   = 0;
    this.movesCount = 0;
    this.rotationsCount = 0;
    this.startMs   = Date.now();
    this.elapsedMs = 0;
    this._attemptRecorded = false;
    this._lastMove = null;

    this.optimalMoves = this._solvePuzzle();
    this.maxMoves     = Math.min(this.optimalMoves + moveMod, 100);

    this.core.setLevel(level);
    this.core._setProgress(0);
    this.core.setScore(this.levelsWon);
  }

  _buildTokens() {
    const n = this.gridSize;
    const tokens = [];
    for (let i = 0; i < n; i++) {
      const shapeName = SHAPE_DATA[i % SHAPE_DATA.length].name;
      for (let j = 0; j < n; j++) tokens.push(shapeName);
    }
    tokens.pop(); tokens.push(null);
    for (let i = tokens.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [tokens[i], tokens[j]] = [tokens[j], tokens[i]];
    }
    this.grid = [];
    for (let r = 0; r < n; r++) this.grid.push(tokens.slice(r * n, (r + 1) * n));
  }

  _computeLayout() {
    const W = this.canvas.width;
    // Centre grid horizontally, push down for HUD
    this.gridOriginX = Math.floor(W / 2 - (this.gridSize * this.cellSize) / 2);
    this.gridOriginY = 200;
  }

  // ── A* solver ─────────────────────────────────────────────────────────────
  _solvePuzzle() {
    const n = this.gridSize;
    const shapeToCol = {};
    SHAPE_DATA.forEach((d, i) => { shapeToCol[d.name] = i % n; });

    const stateKey = g => g.flat().map(v => v || '_').join(',');
    const isGoal = g => {
      for (let c = 0; c < n; c++) {
        const target = SHAPE_DATA[c % SHAPE_DATA.length].name;
        for (let r = 0; r < n; r++) if (g[r][c] !== null && g[r][c] !== target) return false;
      }
      return true;
    };
    const findEmpty = g => {
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (g[r][c] === null) return [r, c];
      return [0, 0];
    };

    if (isGoal(this.grid)) return 0;

    const start = this.grid.map(r => [...r]);
    const queue  = [[start, findEmpty(start), 0]];
    const visited = new Map([[stateKey(start), 0]]);
    const DIRS   = [[0,1],[0,-1],[1,0],[-1,0]];

    for (let qi = 0; qi < queue.length && qi < 8000; qi++) {
      const [g, [er, ec], cost] = queue[qi];
      if (cost >= 20) continue;
      for (const [dr, dc] of DIRS) {
        const nr = er + dr, nc = ec + dc;
        if (nr < 0 || nr >= n || nc < 0 || nc >= n) continue;
        const ng = g.map(r => [...r]);
        ng[er][ec] = ng[nr][nc]; ng[nr][nc] = null;
        if (isGoal(ng)) return cost + 1;
        const k = stateKey(ng), nc2 = cost + 1;
        if (!visited.has(k) || visited.get(k) > nc2) {
          visited.set(k, nc2);
          queue.push([ng, [nr, nc], nc2]);
        }
      }
    }
    return 20;
  }

  _checkVictory() {
    for (let c = 0; c < this.gridSize; c++) {
      const target = SHAPE_DATA[c % SHAPE_DATA.length].name;
      for (let r = 0; r < this.gridSize; r++) {
        if (this.grid[r][c] !== null && this.grid[r][c] !== target) return false;
      }
    }
    return true;
  }

  // ── Main loop ─────────────────────────────────────────────────────────────
  _loop(ts) {
    if (!this._running) return;
    if (this._paused) { this._raf = requestAnimationFrame(t => this._loop(t)); return; }
    const dt = ts - this._lastTs; this._lastTs = ts;
    if (!this.victory && !this.lost) this.elapsedMs += dt;
    this._update();
    this._draw();
    this._raf = requestAnimationFrame(t => this._loop(t));
  }

  _update() {
    if (this._sessionEnded) return;
    if (this.victory && this.endTime > 0) {
      if (performance.now() - this.endTime > 3000) {
        if (this.levelsPlayed >= MAX_SESSION_LEVELS) {
          this._endSession(true);
        } else {
          this._initLevel(this.level + 1);
        }
      }
    }
    if (this.lost && this.endTime > 0) {
      if (performance.now() - this.endTime > 3500) {
        if (this.levelsPlayed >= MAX_SESSION_LEVELS) {
          this._endSession(false);
        } else {
          this.core.toast(this.core.t("engine.retryLevel"), '#5a78dc');
          this._initLevel(this.level);  // retry same level on loss
        }
      }
    }
  }

  // ── Input (mirrors _handle_tile_click) ────────────────────────────────────
  _handleClick(e) {
    if (this.victory || this.lost) return;

    const rect = this.canvas.getBoundingClientRect();
    const mx   = e.clientX - rect.left;
    const my   = e.clientY - rect.top;

    if (this._lastMove && this._pointInRect(mx, my, this._undoButtonRect(this.canvas.width))) {
      this._undoLastMove();
      return;
    }
    if (this._pointInRect(mx, my, this._redoButtonRect(this.canvas.width))) {
      this._redoCurrentLevel();
      return;
    }

    const c = Math.floor((mx - this.gridOriginX) / this.cellSize);
    const r = Math.floor((my - this.gridOriginY) / this.cellSize);
    if (r < 0 || r >= this.gridSize || c < 0 || c >= this.gridSize) return;

    const DIRS = [[0,1],[0,-1],[1,0],[-1,0]];
    for (const [dr, dc] of DIRS) {
      const nr = r + dr, nc = c + dc;
      if (nr < 0 || nr >= this.gridSize || nc < 0 || nc >= this.gridSize) continue;
      if (this.grid[nr][nc] === null) {
        // Save for undo
        this._lastMove = { fromR: r, fromC: c, toR: nr, toC: nc };
        this.grid[nr][nc] = this.grid[r][c];
        this.grid[r][c]   = null;
        this.movesCount++;

        if (this.movesCount >= this.maxMoves) {
          if (this._checkVictory()) { this._onVictory(); }
          else {
            this._onLevelFailed();
          }
        } else if (this._checkVictory()) {
          this._onVictory();
        }

        this.core._setProgress(this.movesCount / this.maxMoves);
        break;
      }
    }
  }

  _onVictory() {
    this.victory    = true;
    this.endTime    = performance.now();
    this._recordCurrentAttempt('completed');
    this.core.showFeedback(true);
    this.core.setScore(this.levelsWon);
    this.core.toast(this.core.t("engine.levelSolved", { level: this.level }), '#06d6a0');
    this.core.spawnParticles();
  }

  _onLevelFailed() {
    this.lost    = true;
    this.endTime = performance.now();
    this._recordCurrentAttempt('failed');
    this.core.showFeedback(false);
  }

  _recordCurrentAttempt(outcome) {
    if (this._attemptRecorded) return;

    this.levelsPlayed++;
    if (outcome === 'completed') {
      this.levelsWon++;
    } else {
      this.levelsFailed++;
      if (outcome === 'redo') this.redoCount++;
    }

    this.totalMoves += this.movesCount;
    this.levelOutcomes.push(outcome);
    this.levelMetrics.push(this._buildLevelMetrics(outcome));
    this._attemptRecorded = true;
  }

  _buildLevelMetrics(outcome = 'completed') {
    const efficiency = Math.min(2, this.optimalMoves / Math.max(this.movesCount, 1));
    const completed = outcome === 'completed' ? 1 : 0;
    return {
      level: this.level,
      outcome,
      completed,
      failed: outcome === 'completed' ? 0 : 1,
      redo: outcome === 'redo' ? 1 : 0,
      solved: completed,
      moves_used: this.movesCount,
      optimal_moves: this.optimalMoves,
      move_efficiency: efficiency,
      time_s: this.elapsedMs / 1000,
      rotations: this.rotationsCount,
    };
  }

  // ── Render ────────────────────────────────────────────────────────────────
  _draw() {
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;


    ctx.fillStyle = '#f5f7fa'; ctx.fillRect(0, 0, W, H);
    this._drawHUD(ctx, W);
    this._drawHints(ctx);
    this._drawGrid(ctx);
    this._drawRedoBtn(ctx, W);
    this._drawUndoBtn(ctx, W);
    this._drawStatus(ctx, W);

    // Progress bar
    const bh = 8, bx = 40, by = H - bh - 10, bw = W - 80;
    ctx.fillStyle = '#ebedf2';
    this._rr(ctx, bx, by, bw, bh, 4); ctx.fill();
    const pct = Math.min(this.movesCount / Math.max(this.maxMoves, 1), 1);
    if (pct > 0) {
      ctx.fillStyle = pct > 0.8 ? '#ef476f' : '#5a78dc';
      this._rr(ctx, bx, by, bw * pct, bh, 4); ctx.fill();
    }
  }

  _drawHUD(ctx, W) {
    ctx.font = 'bold 32px Nunito, sans-serif';
    ctx.fillStyle = '#28283a'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(this.core.t("level.short", { level: this.level }), W / 2, 20);

    ctx.font = 'bold 18px Nunito'; ctx.textAlign = 'right';
    ctx.fillStyle = '#28283a';
    ctx.fillText(this.core.t("engine.timeSeconds", { seconds: Math.floor(this.elapsedMs / 1000) }), W - 20, 30);

    // Steps remaining as friendly indicator (hide Optimal)
    const stepsLeft = Math.max(0, this.maxMoves - this.movesCount);
    ctx.textAlign = 'left';
    ctx.fillStyle = stepsLeft <= 2 ? '#e65a5a' : '#28283a';
    ctx.fillText(this.core.t("engine.stepsLeft", { steps: stepsLeft }), 20, 75);

    // Session progress dots
    const dotX = W / 2 - (MAX_SESSION_LEVELS * 22) / 2;
    for (let i = 0; i < MAX_SESSION_LEVELS; i++) {
      ctx.beginPath(); ctx.arc(dotX + i * 22, 115, 7, 0, Math.PI * 2);
      const outcome = this.levelOutcomes[i];
      ctx.fillStyle = outcome === 'completed'
        ? '#50c878'
        : outcome === 'redo'
          ? '#f59e0b'
          : outcome === 'failed'
            ? '#ef476f'
            : '#dde0ea';
      ctx.fill();
    }
    ctx.font = '12px Nunito'; ctx.fillStyle = '#8c92a0'; ctx.textAlign = 'center';
    ctx.fillText(this.core.t("engine.levelsCount", { count: this.levelsPlayed, total: MAX_SESSION_LEVELS }), W / 2, 135);
  }

  _drawHints(ctx) {
    // Target shape per column shown ABOVE the grid with PASTEL column bg
    for (let c = 0; c < Math.min(this.gridSize, SHAPE_DATA.length); c++) {
      // Pastel column background
      const colX = this.gridOriginX + c * this.cellSize;
      const colH = this.gridSize * this.cellSize;
      ctx.fillStyle = SHAPE_DATA[c].color + '18';  // ~10% alpha
      ctx.fillRect(colX, this.gridOriginY, this.cellSize, colH);

      // Bigger target shape hint (50px)
      const cx = colX + this.cellSize / 2;
      const cy = this.gridOriginY - 45;
      this._drawShape(ctx, SHAPE_DATA[c].name, cx, cy, 50, '#b4b8c8');

      // Column-completion check: if all shapes in column match target, glow green
      const target = SHAPE_DATA[c % SHAPE_DATA.length].name;
      const colComplete = Array.from({ length: this.gridSize }, (_, r) => this.grid[r]?.[c]).every(v => v === null || v === target);
      if (colComplete && !this.grid.flat().every(v => v === null)) {
        ctx.save();
        ctx.fillStyle = 'rgba(34,197,94,0.08)';
        ctx.fillRect(colX, this.gridOriginY, this.cellSize, colH);
        ctx.strokeStyle = '#22c55e'; ctx.lineWidth = 2;
        ctx.strokeRect(colX, this.gridOriginY, this.cellSize, colH);
        // Checkmark
        ctx.font = 'bold 18px Nunito'; ctx.fillStyle = '#22c55e';
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillText('✓', cx, this.gridOriginY + colH + 4);
        ctx.restore();
      }
    }
  }

  _drawGrid(ctx) {
    for (let r = 0; r < this.gridSize; r++) {
      for (let c = 0; c < this.gridSize; c++) {
        const x = this.gridOriginX + c * this.cellSize;
        const y = this.gridOriginY + r * this.cellSize;
        const cs = this.cellSize;
        const shape = this.grid[r][c];

        ctx.strokeStyle = '#dcdee6'; ctx.lineWidth = 1;
        ctx.strokeRect(x, y, cs, cs);

        if (shape === null) {
          ctx.fillStyle = '#e8eaf0'; ctx.fillRect(x + 2, y + 2, cs - 4, cs - 4);
          continue;
        }

        ctx.fillStyle = '#fff'; ctx.fillRect(x, y, cs, cs);
        const data = SHAPE_DATA.find(d => d.name === shape);
        if (data) this._drawShape(ctx, shape, x + cs / 2, y + cs / 2, 80, data.color, true);
      }
    }
  }

  _drawStatus(ctx, W) {
    if (this.victory) {
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(0, W/3, W, 80);
      ctx.font = 'bold 34px Nunito'; ctx.fillStyle = '#50c878';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.core.t("engine.won"), W / 2, W / 3 + 40);
      ctx.font = '20px Nunito'; ctx.fillStyle = '#fff';
      ctx.fillText(this.core.t("engine.nextLevelCountdown"), W / 2, W / 3 + 70);
      ctx.restore();
    } else if (this.lost) {
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(0, W/3, W, 80);
      ctx.font = 'bold 30px Nunito'; ctx.fillStyle = '#FCD34D';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.core.t("engine.almostRetry"), W / 2, W / 3 + 40);
      ctx.font = '20px Nunito'; ctx.fillStyle = '#fff';
      ctx.fillText(this.core.t("engine.restartCountdown"), W / 2, W / 3 + 70);
      ctx.restore();
    }
  }

  // Undo button
  _drawUndoBtn(ctx, W) {
    if (this.victory || this.lost || !this._lastMove) return;
    const { x: btnX, y: btnY, w: btnW, h: btnH } = this._undoButtonRect(W);
    ctx.fillStyle = 'rgba(109,40,217,0.12)';
    this._rr(ctx, btnX, btnY, btnW, btnH, 8); ctx.fill();
    ctx.strokeStyle = '#6D28D9'; ctx.lineWidth = 1.5;
    this._rr(ctx, btnX, btnY, btnW, btnH, 8); ctx.stroke();
    ctx.font = 'bold 14px Nunito'; ctx.fillStyle = '#6D28D9';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(this.core.t("engine.undo"), btnX + btnW / 2, btnY + btnH / 2);
  }

  _drawRedoBtn(ctx, W) {
    if (this.victory || this.lost) return;
    const { x: btnX, y: btnY, w: btnW, h: btnH } = this._redoButtonRect(W);
    ctx.fillStyle = 'rgba(239,68,68,0.10)';
    this._rr(ctx, btnX, btnY, btnW, btnH, 8); ctx.fill();
    ctx.strokeStyle = '#dc2626'; ctx.lineWidth = 1.5;
    this._rr(ctx, btnX, btnY, btnW, btnH, 8); ctx.stroke();
    ctx.font = 'bold 14px Nunito'; ctx.fillStyle = '#dc2626';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(this.core.t("engine.redo"), btnX + btnW / 2, btnY + btnH / 2);
  }

  _undoButtonRect(W) {
    return { x: Math.max(20, W - 106), y: 90, w: 86, h: 34 };
  }

  _redoButtonRect(W) {
    const undo = this._undoButtonRect(W);
    return { x: Math.max(20, undo.x - 98), y: undo.y, w: 88, h: 34 };
  }

  _pointInRect(x, y, rect) {
    return x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h;
  }

  _undoLastMove() {
    if (!this._lastMove) return;
    const m = this._lastMove;
    this.grid[m.fromR][m.fromC] = this.grid[m.toR][m.toC];
    this.grid[m.toR][m.toC] = null;
    this.movesCount = Math.max(0, this.movesCount - 1);
    this._lastMove = null;
    this.core._setProgress(this.movesCount / this.maxMoves);
  }

  _redoCurrentLevel() {
    if (this._sessionEnded || this.victory || this.lost) return;
    this._recordCurrentAttempt('redo');
    if (this.levelsPlayed >= MAX_SESSION_LEVELS) {
      this._endSession(false);
      return;
    }
    this.core.toast(this.core.t("engine.retryLevel"), '#5a78dc');
    this._initLevel(this.level);
  }

  /**
   * Draw shape (mirrors ShapesGame._draw_shape exactly)
   * filled = true → fill; false → outline (thickness ~5)
   */
  _drawShape(ctx, shapeName, cx, cy, size, color, filled = false) {
    const s = size / 2;
    ctx.fillStyle   = color;
    ctx.strokeStyle = color;
    ctx.lineWidth   = filled ? 0 : 5;
    ctx.beginPath();
    switch (shapeName) {
      case 'carre':
        ctx.rect(cx - s, cy - s, size, size); break;
      case 'triangle':
        ctx.moveTo(cx, cy - s); ctx.lineTo(cx - s, cy + s); ctx.lineTo(cx + s, cy + s);
        ctx.closePath(); break;
      case 'cercle':
        ctx.arc(cx, cy, s, 0, Math.PI * 2); break;
      case 'etoile':
        for (let i = 0; i < 10; i++) {
          const a = (i * 36 * Math.PI) / 180;
          const r = i % 2 === 0 ? s : s / 2;
          if (i === 0) ctx.moveTo(cx + r * Math.sin(a), cy - r * Math.cos(a));
          else         ctx.lineTo(cx + r * Math.sin(a), cy - r * Math.cos(a));
        }
        ctx.closePath(); break;
      default:
        ctx.arc(cx, cy, s, 0, Math.PI * 2);
    }
    if (filled) ctx.fill(); else ctx.stroke();
  }

  _rr(ctx, x, y, w, h, r) { roundRect(ctx, x, y, w, h, r); }

  _maxLevelReached() {
    const metricLevels = this.levelMetrics.map(m => m.level);
    return Math.max(this.level, ...metricLevels);
  }

  _sessionAverages() {
    if (this.levelMetrics.length === 0) {
      return { avgEff: 0, avgTime: 0, avgRot: 0, avgMoves: 0 };
    }
    const n = this.levelMetrics.length;
    return {
      avgEff: this.levelMetrics.reduce((a, b) => a + b.move_efficiency, 0) / n,
      avgTime: this.levelMetrics.reduce((a, b) => a + b.time_s, 0) / n,
      avgRot: this.levelMetrics.reduce((a, b) => a + b.rotations, 0) / n,
      avgMoves: this.levelMetrics.reduce((a, b) => a + b.moves_used, 0) / n,
    };
  }

  _buildSessionPayload(duration, completed, sessionOutcome) {
    const accuracy = this.levelsWon / Math.max(this.levelsPlayed, 1);
    const averages = this._sessionAverages();
    const activeMoves = this._attemptRecorded ? 0 : this.movesCount;
    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round(duration * 10) / 10,
      totalActions: this.levelsPlayed,
      correct: this.levelsWon,
      incorrect: this.levelsFailed,
      completed,
      level: this.level,
      max_level_reached: this._maxLevelReached(),
      roundsPlayed: this.levelsPlayed,
      roundsPassed: this.levelsWon,
      sessionOutcome,
      roundDetails: this.levelMetrics,
    });

    return {
      ...base,
      Total_Actions: this.levelsPlayed,
      Correct_Responses: this.levelsWon,
      Incorrect_Responses: this.levelsFailed,
      Touch_Interactions: this.totalMoves + activeMoves + this.redoCount,
      Reaction_Time: 0,
      levels_played: this.levelsPlayed,
      levels_won: this.levelsWon,
      levels_completed: this.levelsWon,
      levels_failed: this.levelsFailed,
      redo_count: this.redoCount,
      retry_count: this.redoCount,
      total_level_attempts: this.levelsPlayed,
      total_moves: this.totalMoves,
      session_accuracy: Math.round(accuracy * 10000) / 10000,
      avg_move_efficiency: Math.round(averages.avgEff * 10000) / 10000,
      avg_time_per_level: Math.round(averages.avgTime * 10) / 10,
      avg_moves_per_level: Math.round(averages.avgMoves * 10) / 10,
      avg_rotations: Math.round(averages.avgRot * 10) / 10,
    };
  }

  // ── Session end ────────────────────────────────────────────────────────────
  _endSession(lastVictory) {
    if (this._sessionEnded) return;
    this._sessionEnded = true;
    const duration   = (Date.now() - this.sessStartMs) / 1000;
    const accuracy   = this.levelsWon / Math.max(this.levelsPlayed, 1);

    const payload = this._buildSessionPayload(
      duration,
      lastVictory,
      lastVictory ? 'completed' : 'not_completed'
    );


    const results = {
      gameName: 'shapes',
      accuracy,
      payload,
      displayMetrics: [
        { label: 'Niveaux reussis', value: `${this.levelsWon} / ${this.levelsPlayed}` },
        { label: 'Niveaux echoues', value: this.levelsFailed },
        { label: 'Redos', value: this.redoCount },
        { label: 'Duree session', value: `${duration.toFixed(0)} s` },
      ],
    };
    this.core.onGameComplete(results);
  }

  getPartialResults() {
    if (this._sessionEnded || this.victory || this.lost || (this.levelsPlayed === 0 && this.totalMoves === 0 && this.movesCount === 0)) return null;
    
    const duration   = (Date.now() - this.sessStartMs) / 1000;
    const accuracy   = this.levelsWon / Math.max(this.levelsPlayed, 1);
    const payload = this._buildSessionPayload(duration, false, 'interrupted');

    return {
      gameName: 'shapes',
      accuracy,
      payload,
      displayMetrics: [],
    };
  }
}
