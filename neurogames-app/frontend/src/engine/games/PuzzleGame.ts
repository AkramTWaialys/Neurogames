// @ts-nocheck
import { NeuroAPI } from '../neuroApi';
import { roundRect, lightenHex } from '../utils';

/**
 * PuzzleGame.js — Geometric Memory Puzzle (faithful web port of puzzle/puzzle_game.py)
 *
 * ══════════════════════════════════════════════════════════════════
 * LAYOUT (mirrors Pygame constants.py exactly):
 *
 *   ┌─────────────────────────────────────────────────────── HEADER (58px) ──┐
 *   │ Score / Level / Streak / Timer                                         │
 *   ├────────────────────────────── TOP ZONE (game_grid_zone) ───────────────┤
 *   │  Phase 1: TARGET grid shown here (centred, full width)                 │
 *   │  Phase 2: BUILD grid shown here (same zone, centred)                   │
 *   ├────────────────────────────── BOTTOM ZONE (inventory_zone_rect) ───────┤
 *   │  Pieces arranged horizontally (randomised & rotated)                   │
 *   └────────────────────────────────────────────────────────────────────────┘
 *
 * The grid zone height = LABEL_PAD (27) + grid_h + 30
 * Inventory zone starts immediately below the grid zone (+ 10px gap).
 *
 * Grid size (mirrors compute_grid_params):
 *   levelIdx 0 : bounding-box of the generated shapes
 *   levelIdx 1+: cols = min(4 + (levelIdx-1), 12), rows = 5
 *   cell_size = min(zone_w // cols, zone_h // rows, 50)
 *
 * Piece generation (mirrors LevelDifficultyState + ProceduralLevelGenerator):
 *   nb_shapes = 3 (initial)
 *   nb_easy   = 3 (decreases toward 1 with advance; then nb_shapes++)
 *   nb_complex= 0 (increases with advance)
 *   Pieces are drawn from SHAPE_LIBRARY (polyominoes), placed on the grid,
 *   then coloured from COLOR_PALETTE.
 *
 * ══════════════════════════════════════════════════════════════════
 */

// ── Shape Library (mirrors SHAPE_LIBRARY from constants.py) ────────────────
const SL_EASY = [
  [[0,0]],
  [[0,0],[1,0]],
  [[0,0],[1,0],[2,0]],
  [[0,0],[1,0],[2,0],[3,0]],
  [[0,0],[1,0],[0,1],[1,1]],
  [[0,0],[1,0],[0,1]],
  [[0,0],[0,1],[0,2],[1,2]],
];
const SL_HARD = [
  [[0,0],[1,0],[2,0],[1,1]],
  [[1,0],[0,1],[1,1],[2,1],[1,2]],
  [[0,0],[1,0],[1,1],[2,1]],
  [[1,0],[2,0],[0,1],[1,1]],
  [[0,0],[0,1],[0,2],[0,3],[1,3]],
  [[0,0],[1,0],[1,1],[2,1],[2,2]],
  [[0,0],[1,0],[2,0],[3,0],[1,1]],
  [[0,0],[1,0],[1,1],[1,2],[2,2]],
];

// ── Colour palette (mirrors COLOR_PALETTE) ──────────────────────────────────
const COLOR_PALETTE_PZ = [
  '#ff6961','#79d97b','#fdfd96','#84b6f4','#fdcae1','#cf9fff','#ffb366','#66d9e8',
];
const DISTRACTOR_COLORS_PZ = ['#a0a0aa','#beaaa0','#aa9fbe'];

// ── Rotation (mirrors _apply_rotation from utils.py: 90° CW each step) ──────
function rotateCells90(cells) {
  const maxR = Math.max(...cells.map(([,r]) => r));
  return cells.map(([c, r]) => [maxR - r, c]);
}
function applyRotation(cells, n) {
  let c = cells.map(r => [...r]);
  for (let i = 0; i < (n % 4); i++) c = rotateCells90(c);
  return c;
}

// ── LevelDifficultyState (mirrors generator.py) ──────────────────────────────
class LevelDifficultyState {
  constructor() {
    this.nbShapes  = 2;   // start with only 2 pieces
    this.nbEasy    = 2;
    this.nbComplex = 0;
    this._levelIdx = 0;
  }
  get levelIdx() { return this._levelIdx; }
  get distractorCount() {
    if (this._levelIdx < 6) return 0;
    return Math.min(Math.floor((this._levelIdx - 6) / 3) + 1, 3);
  }
  get densityLevel() {
    return Math.min(Math.max(0.10 + this._levelIdx * 0.025, 0.10), 0.90);
  }
  advance() {
    this._levelIdx++;
    if (this._levelIdx <= 2) {
      // Gentle ramp: stay all-easy, add a piece at level 2
      if (this._levelIdx === 2) { this.nbShapes = 3; this.nbEasy = 3; }
    } else if (this.nbEasy > 1) { this.nbEasy--; this.nbComplex++; }
    else { this.nbShapes++; this.nbEasy = this.nbShapes; this.nbComplex = 0; }
  }
  snapshot() { return [this.nbEasy, this.nbComplex]; }
}

// ── Procedural Level Generator (mirrors ProceduralLevelGenerator) ─────────────

function chebyshevMin(ox, oy, rotCells, occupied) {
  if (occupied.size === 0) return 999;
  let minD = 999;
  for (const [c, r] of rotCells) {
    const ac = ox + c, ar = oy + r;
    for (const occStr of occupied) {
      const [oc, or] = occStr.split(',').map(Number);
      const d = Math.max(Math.abs(ac - oc), Math.abs(ar - or));
      if (d < minD) minD = d;
    }
  }
  return minD;
}

function scoreOrigin(ox, oy, rotCells, occupied, gridCols, gridRows) {
  if (occupied.size === 0) {
    const cx = Math.abs(ox - Math.floor(gridCols / 2));
    const cy = Math.abs(oy - Math.floor(gridRows / 2));
    const centerBonus = 1.0 - (cx + cy) / (gridCols + gridRows);
    return centerBonus + 0.1 * Math.random();
  }
  return 1.0 + 0.2 * Math.random();
}

// ── Grid-fit check: can the shape fit in at least one rotation? ─────────────
function canFitGrid(baseCells, gridCols, gridRows) {
  for (let rot = 0; rot < 4; rot++) {
    const rotated = applyRotation(baseCells, rot);
    const w = Math.max(...rotated.map(([c]) => c)) + 1;
    const h = Math.max(...rotated.map(([,r]) => r)) + 1;
    if (w <= gridCols && h <= gridRows) return true;
  }
  return false;
}

function generateLevel(difficulty, gridCols, gridRows) {
  const [nbEasy, nbComplex] = difficulty.snapshot();
  const nDistractors = difficulty.distractorCount;

  const shapeDefs = pickShapes(nbEasy, nbComplex, new Set(), false, gridCols, gridRows);
  const [occupied, templates] = placeShapes(shapeDefs, gridCols, gridRows, false, 0);

  const allTemplates = [...templates];
  if (nDistractors > 0) {
    const usedNames = new Set(shapeDefs.map((_, i) => i));
    const distDefs  = pickShapes(nDistractors, 0, usedNames, true, gridCols, gridRows);
    const [, distTpls] = placeShapes(distDefs, gridCols, gridRows, true, templates.length, new Set(occupied));
    allTemplates.push(...distTpls);
  }
  return allTemplates;
}

function pickShapes(nbEasy, nbComplex, excludeIdx, allowAdvanced, gridCols = 12, gridRows = 5) {
  // Filter shapes that can fit the grid
  const easyPool = SL_EASY.filter((cells, k) => !excludeIdx.has(k) && canFitGrid(cells, gridCols, gridRows));
  const hardPool = SL_HARD.filter((cells) => canFitGrid(cells, gridCols, gridRows));
  const pool = allowAdvanced ? [...easyPool, ...hardPool] : [...easyPool];
  // Fallback: always have at least MONO and DOMINO
  const fallback = SL_EASY.filter(cells => canFitGrid(cells, gridCols, gridRows));
  const safePool = pool.length > 0 ? pool : (fallback.length > 0 ? fallback : SL_EASY.slice(0, 2));

  const result = [];
  const used = new Set();
  for (let i = 0; i < nbEasy + nbComplex; i++) {
    const avail = safePool.filter((_, k) => !used.has(k));
    const src   = avail.length > 0 ? avail : safePool;
    const idx   = Math.floor(Math.random() * src.length);
    result.push([...src[idx]]);
    used.add(idx);
  }
  return result;
}

function placeShapes(shapeDefs, gridCols, gridRows, isDistractor, colorOffset, startOccupied) {
  const MAX_CLUSTER_GAP = 1;
  const TOP_K_FRACTION = 0.30;
  
  for (let attempt = 0; attempt < 1000; attempt++) {
    const occupied = new Set(startOccupied ? [...startOccupied] : []);
    const templates = [];
    let success = true;

    for (let idx = 0; idx < shapeDefs.length; idx++) {
      const baseCells = shapeDefs[idx];

      // Only pick from rotations that actually fit the grid
      const validRotations = [];
      for (let r = 0; r < 4; r++) {
        const rc = applyRotation(baseCells, r);
        const w = Math.max(...rc.map(([c]) => c)) + 1;
        const h = Math.max(...rc.map(([,row]) => row)) + 1;
        if (w <= gridCols && h <= gridRows) validRotations.push(r);
      }
      if (validRotations.length === 0) { success = false; break; }

      const numRot    = validRotations[Math.floor(Math.random() * validRotations.length)];
      const rotCells  = applyRotation(baseCells, numRot);
      const maxC      = Math.max(...rotCells.map(([c]) => c));
      const maxR      = Math.max(...rotCells.map(([,r]) => r));

      const allOrigins = [];
      for (let ox = 0; ox <= gridCols - maxC - 1; ox++) {
        for (let oy = 0; oy <= gridRows - maxR - 1; oy++) {
          allOrigins.push([ox, oy]);
        }
      }

      let candidates = [];
      if (occupied.size === 0) {
        // First piece: prefer center
        const cx = Math.floor(gridCols / 2), cy = Math.floor(gridRows / 2);
        candidates = allOrigins.filter(([ox, oy]) => {
          return rotCells.every(([c, r]) => !occupied.has(`${ox + c},${oy + r}`));
        });
        candidates.sort((a, b) => (Math.abs(a[0] - cx) + Math.abs(a[1] - cy)) - (Math.abs(b[0] - cx) + Math.abs(b[1] - cy)));
        const topCenter = Math.max(1, Math.floor(candidates.length / 5));
        candidates = candidates.slice(0, topCenter);
      } else {
        // Subsequent pieces: MUST touch existing shapes (Chebyshev distance <= 1)
        candidates = allOrigins.filter(([ox, oy]) => {
          const noOverlap = rotCells.every(([c, r]) => !occupied.has(`${ox + c},${oy + r}`));
          if (!noOverlap) return false;
          return chebyshevMin(ox, oy, rotCells, occupied) <= MAX_CLUSTER_GAP;
        });

        if (candidates.length === 0) {
          // Fallback if no touching spots available
          candidates = allOrigins.filter(([ox, oy]) => {
            return rotCells.every(([c, r]) => !occupied.has(`${ox + c},${oy + r}`));
          });
        }
      }

      if (candidates.length === 0) { success = false; break; }

      // Score candidates and pick top K
      const scored = candidates.map(([ox, oy]) => ({
        ox, oy, score: scoreOrigin(ox, oy, rotCells, occupied, gridCols, gridRows)
      }));
      scored.sort((a, b) => b.score - a.score);
      const topK = Math.max(1, Math.floor(scored.length * TOP_K_FRACTION));
      const pool = scored.slice(0, topK);
      const chosen = pool[Math.floor(Math.random() * pool.length)];

      const ox = chosen.ox, oy = chosen.oy;
      const absCells = rotCells.map(([c, r]) => [ox + c, oy + r]);
      absCells.forEach(([c,r]) => occupied.add(`${c},${r}`));

      const color = isDistractor
        ? DISTRACTOR_COLORS_PZ[(colorOffset + idx) % DISTRACTOR_COLORS_PZ.length]
        : COLOR_PALETTE_PZ[(colorOffset + idx) % COLOR_PALETTE_PZ.length];

      templates.push({ color, cells: rotCells, targetX: ox, targetY: oy, isDistractor });
    }
    
    if (success) return [occupied, templates];
  }
  
  // Hard fallback if 1000 attempts fail — find a rotation that fits
  let fallbackRot = applyRotation(shapeDefs[0], 0);
  for (let r = 0; r < 4; r++) {
    const rc = applyRotation(shapeDefs[0], r);
    const w = Math.max(...rc.map(([c]) => c)) + 1;
    const h = Math.max(...rc.map(([,row]) => row)) + 1;
    if (w <= gridCols && h <= gridRows) { fallbackRot = rc; break; }
  }
  const fallbackSet = new Set(startOccupied ? [...startOccupied] : []);
  fallbackRot.forEach(([c, r]) => fallbackSet.add(`${c},${r}`));
  return [fallbackSet, [{
    color: COLOR_PALETTE_PZ[colorOffset % COLOR_PALETTE_PZ.length],
    cells: fallbackRot, targetX: 0, targetY: 0, isDistractor: isDistractor
  }]];
}

// ── Layout (mirrors compute_grid_params — gentler ramp for early levels) ──────
const LEVEL_GRID = { 0: [3,3], 1: [3,4], 2: [4,4], 3: [4,5], 4: [5,5] };

function computeGridParams(levelIdx, canvasW, canvasH, shapeCols?, shapeRows?) {
  const HEADER     = 58;
  const LABEL_PAD  = 27;
  const FORMULA_BASE_COLS = 6;   // Level 5 starts at 6 cols
  const FIXED_ROWS   = 5;
  const MAX_COLS     = 12;
  const CELL_SIZE_MAX = 50;
  const MARGIN = 40;

  let cols, rows;
  if (LEVEL_GRID[levelIdx]) {
    [cols, rows] = LEVEL_GRID[levelIdx];
  } else {
    // Level 5+: each level adds 1 column, rows stay 5
    cols = Math.min(FORMULA_BASE_COLS + (levelIdx - 5), MAX_COLS);
    cols = Math.max(cols, FORMULA_BASE_COLS);
    rows = FIXED_ROWS;
  }

  const zoneW  = canvasW - MARGIN * 2 - 30;
  const zoneH  = 250;
  const csW    = Math.floor(zoneW / cols);
  const csH    = Math.floor(zoneH / rows);
  const cs     = Math.min(csW, csH, CELL_SIZE_MAX);

  const gridW  = cols * cs;
  const gridH  = rows * cs;

  // game_grid_zone
  const gridZoneX = MARGIN;
  const gridZoneY = HEADER;
  const gridZoneW = canvasW - MARGIN * 2;
  const gridZoneH = LABEL_PAD + gridH + 30;

  // plank/target grid origin = centred inside grid zone
  const gridOX = gridZoneX + Math.floor((gridZoneW - gridW) / 2);
  const gridOY = gridZoneY + LABEL_PAD + 15;

  // inventory zone
  const invY = gridZoneY + gridZoneH + 10;
  const invH = Math.max(60, canvasH - invY - 10);

  return { cols, rows, cs, gridOX, gridOY, gridZoneX, gridZoneY, gridZoneW, gridZoneH, invY, invH, canvasW };
}

// ════════════════════════════════════════════════════════════════
// PuzzleGame class
// ════════════════════════════════════════════════════════════════
export class PuzzleGame {
  constructor(canvas, player, core) {
    this.canvas = canvas;
    this.ctx    = canvas.getContext('2d');
    this.player = player;
    this.core   = core;

    this.diffState  = new LevelDifficultyState();
    this.score      = 0;
    this.phase      = 1;
    this.phaseStartMs = 0;

    // Templates (from generator) and player pieces
    this.currentTemplates = [];
    this.distractorTemplates = [];
    this.playerPieces = [];

    // Layout (recomputed each level)
    this.layout = null;

    // Phase 1
    this.scannedIdx = new Set();
    this.scanGlow   = {};

    // Phase 2
    this.draggingPiece = null;
    this.dragOffX = 0; this.dragOffY = 0;
    this.hintsRevealed = 0;

    // Tutorial banners (shown once per session)
    this._shownRotationTutorial    = false;
    this._shownDistractorTutorial  = false;
    this._tutorialBanner           = null;  // string | null  (blocks game while set)

    // Mastery metrics
    this._movesUsed         = 0;
    this._backtracks        = 0;
    this._errors            = 0;
    this._invalidClicks     = 0;
    this._rotations         = 0;
    this._correctPlacements = 0;
    this._prevLevelErrors   = 0;
    this._sessHistory       = {}; // Reset every session
    this.levelMetrics       = [];

    // UI
    this.message      = '';
    this.messageMs    = 0;
    this.floatingPraise = null;
    this.glowTimers   = {};
    this.successPause    = false;
    this.successPauseMs  = 0;
    this.gameOver     = false;

    this._praise = [
      this.core.t('engine.praise.1'),
      this.core.t('engine.praise.2'),
      this.core.t('engine.praise.3'),
      this.core.t('engine.praise.4'),
      this.core.t('engine.praise.5'),
    ];
    this.sessStartMs = 0;

    this._raf    = null;
    this._lastTs = null;
    this._running = false;
    this._paused = false;

    this._onDown = this._pointerDown.bind(this);
    this._onMove = this._pointerMove.bind(this);
    this._onUp   = this._pointerUp.bind(this);
    this._onCtx  = e => { e.preventDefault(); this._rightClick(e); };
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  start() {
    this.sessStartMs   = Date.now();
    this.score         = 0;
    this.gameOver      = false;
    this.diffState     = new LevelDifficultyState();
    this._prevLevelErrors = 0;
    this._masteryStreak   = this._getStreak(0);
    this.levelMetrics     = [];

    this.canvas.addEventListener('pointerdown', this._onDown);
    this.canvas.addEventListener('pointermove', this._onMove);
    this.canvas.addEventListener('pointerup',   this._onUp);
    this.canvas.addEventListener('contextmenu', this._onCtx);

    this._running = true;
    this.core.setLevel(1);
    this.core._setProgress(0);

    this._loadLevel();
    this.phase        = 1;
    this.phaseStartMs = Date.now();
    this.message      = this.core.t('engine.scanInstruction');
    this.messageMs    = Date.now();

    this._lastTs = performance.now();
    this._loop(this._lastTs);
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this.canvas.removeEventListener('pointerdown', this._onDown);
    this.canvas.removeEventListener('pointermove', this._onMove);
    this.canvas.removeEventListener('pointerup',   this._onUp);
    this.canvas.removeEventListener('contextmenu', this._onCtx);
  }

  pause() { this._paused = true; }
  resume() { this._paused = false; this._lastTs = performance.now(); }

  onResize() {
    // Recompute layout without regenerating pieces
    if (this.currentTemplates.length > 0) {
      this._computeLayout();
      this._placeInventory();
    }
  }

  // ── Level generation ───────────────────────────────────────────────────────

  // ── Level-dependent timers ──
  get _phase1Time() {
    const lvl = this.diffState.levelIdx;
    if (lvl <= 1) return 25;
    if (lvl <= 3) return 22;
    return 20;
  }
  get _phase2Time() {
    const lvl = this.diffState.levelIdx;
    if (lvl <= 1) return 40;
    if (lvl <= 3) return 35;
    return 30;
  }

  _loadLevel() {
    const lvl = this.diffState.levelIdx;

    // All levels: grid size comes from the lookup / formula
    this._computeLayout(null, null, lvl);
    const { cols: gridCols, rows: gridRows } = this.layout;

    const all = generateLevel(this.diffState, gridCols, gridRows);
    this.currentTemplates    = all.filter(t => !t.isDistractor);
    this.distractorTemplates = all.filter(t =>  t.isDistractor);

    this._buildPlayerPieces();
    this._placeInventory();

    // Reset per-level state
    this.scannedIdx.clear(); this.scanGlow = {};
    this.hintsRevealed    = 0;
    this._correctPlacements = 0;
    this.floatingPraise   = null;
    this.glowTimers       = {};
    this.successPause     = false;
    this.draggingPiece    = null;
    this._resetMetrics();

    this.core.setLevel(lvl + 1);
    this.core._setProgress(0);

    // Tutorial banners — show once per session on the first level that introduces the feature
    if (lvl >= 3 && !this._shownRotationTutorial) {
      this._shownRotationTutorial = true;
      this._tutorialBanner = this.core.t('engine.rotateTutorial');
    }
    if (this.distractorTemplates.length > 0 && !this._shownDistractorTutorial) {
      this._shownDistractorTutorial = true;
      this._tutorialBanner = this.core.t('engine.distractorTutorial');
    }
  }

  _computeLayout(cols, rows, levelIdx) {
    const l = computeGridParams(
      levelIdx != null ? levelIdx : this.diffState.levelIdx,
      this.canvas.width, this.canvas.height,
      cols, rows
    );
    this.layout = l;
  }

  _buildPlayerPieces() {
    const lvl = this.diffState.levelIdx;
    this.playerPieces = this.currentTemplates.map((tp, i) => {
      // Level-dependent rotation: none at 0-2, light at 3, random at 4+
      let rots;
      if (lvl <= 2)      rots = 0;
      else if (lvl === 3) rots = Math.floor(Math.random() * 2);
      else               rots = Math.floor(Math.random() * 4);
      const cells = applyRotation(tp.cells, rots);
      return {
        id: i, cells, color: tp.color,
        state: 'inventory', gridX: 0, gridY: 0,
        pixelX: 0, pixelY: 0, dragging: false,
        dragCount: 0, placed: false,
      };
    });
    // Add distractors to player pieces
    this.distractorTemplates.forEach((tp, i) => {
      const rots  = Math.floor(Math.random() * 4);
      const cells = applyRotation(tp.cells, rots);
      this.playerPieces.push({
        id: this.currentTemplates.length + i, cells, color: tp.color,
        state: 'inventory', gridX: 0, gridY: 0,
        pixelX: 0, pixelY: 0, dragging: false,
        dragCount: 0, placed: false, isDistractor: true,
      });
    });
  }

  _placeInventory() {
    if (!this.layout || !this.playerPieces.length) return;
    const { invY, invH, canvasW, cs } = this.layout;
    const n = this.playerPieces.length;
    if (!n) return;
    const spacing = Math.floor(canvasW / (n + 1));

    // Shuffle order for display
    const indices = [...Array(n).keys()].sort(() => Math.random() - 0.5);
    indices.forEach((pi, si) => {
      const p = this.playerPieces[pi];
      const shapeW = (Math.max(...p.cells.map(([c])=>c)) + 1) * cs;
      const shapeH = (Math.max(...p.cells.map(([,r])=>r)) + 1) * cs;
      p.pixelX = spacing * (si + 1) - shapeW / 2;
      p.pixelY = invY + (invH - shapeH) / 2;
    });
  }

  // ── Main loop ──────────────────────────────────────────────────────────────

  _loop(ts) {
    if (!this._running) return;
    if (this.gameOver) return;   // Stop loop entirely once the game is over
    if (this._paused) { this._raf = requestAnimationFrame(t => this._loop(t)); return; }
    const dt = ts - this._lastTs; this._lastTs = ts;
    this._update();
    this._draw();
    this._raf = requestAnimationFrame(t => this._loop(t));
  }

  _update() {
    if (this.gameOver || this._tutorialBanner) return;

    if (this.successPause) {
      if (Date.now() - this.successPauseMs > 1500) {
        this.successPause = false;
        this._transitionPhase();
      }
      return;
    }

    const secs = (Date.now() - this.phaseStartMs) / 1000;

    const p1 = this._phase1Time;
    const p2 = this._phase2Time;

    if (this.phase === 1) {
      const allScanned = this.scannedIdx.size >= this.currentTemplates.length;
      if (secs >= p1 || allScanned) {
        this.successPause   = true;
        this.successPauseMs = Date.now();
        this.message = allScanned ? this.core.t('engine.allScanned') : this.core.t('engine.memorizationDone');
        this.messageMs = Date.now();
      }
      this.core._setProgress(Math.min(secs / p1, 1));
    } else {
      if (secs >= p2 - 5) this.hintsRevealed = Math.max(this.hintsRevealed, 2);
      else if (secs >= p2 - 10) this.hintsRevealed = Math.max(this.hintsRevealed, 1);
      if (secs >= p2) { this._handleTimeout(); return; }
      this.core._setProgress(Math.min(secs / p2, 1));
    }
  }

  // ── Phase transition ───────────────────────────────────────────────────────

  _transitionPhase() {
    if (this.phase === 1) {
      this.phase = 2;
      this.phaseStartMs = Date.now();
      this.hintsRevealed = 0;
      this.message  = this.core.t('engine.goRebuild');
      this.messageMs = Date.now();
      this.playerPieces.forEach(p => { p.state = 'inventory'; p.placed = false; });
    } else {
      this._computeMasteryAndAdvance((Date.now() - this.phaseStartMs) / 1000);
    }
  }

  _computeMasteryAndAdvance(timeUsed) {
    const minMoves      = this.currentTemplates.length;
    const totalMoves    = Math.max(this._movesUsed, 1);
    const masteryIndex  = Math.min(1, this._correctPlacements / Math.max(minMoves, 1));

    const levelScore    = 100
      + (this.scannedIdx.size >= this.currentTemplates.length ? 100 : 0)
      + (this._errors < this._prevLevelErrors ? 100 : 0)
      + (this.hintsRevealed === 0 ? 50 : 0)
      + this._correctPlacements * 50;

    this.score += levelScore;
    this._prevLevelErrors = this._errors;

    this._updateStreak(this.diffState.levelIdx, masteryIndex);
    const streak           = this._getStreak(this.diffState.levelIdx);
    this._masteryStreak    = streak;

    this.levelMetrics.push({
      timeUsed, masteryIndex, minMoves, totalMoves,
      invalidClicks: this._invalidClicks, backtracks: this._backtracks, errors: this._errors,
      correctPlacements: this._correctPlacements
    });

    if (streak >= 3) {
      this.diffState.advance();
      const pct = Math.round(masteryIndex * 100);
      this.message = this.core.t('engine.puzzleLevelUp', { level: this.diffState.levelIdx + 1, pct, score: levelScore });
      this._masteryStreak = 0;
    } else {
      const pct = Math.round(masteryIndex * 100);
      this.message = this.core.t('engine.puzzleRetry', { pct, streak, score: levelScore });
    }

    this.messageMs    = Date.now();
    this.phase        = 1;
    this.phaseStartMs = Date.now();
    this.core.setScore(this.score);

    this._loadLevel();
  }

  _handleTimeout() {
    if (this.gameOver) return;
    this.gameOver = true;
    this.core.toast('⏰ Temps écoulé !', '#ef476f');
    
    // Capture the level being played at the time of timeout
    const completedLevel = this.diffState.levelIdx + 1;

    // Push the failed level's metrics
    const minMoves = this.currentTemplates.length;
    const totalMoves = Math.max(this._movesUsed, 1);
    const masteryIndex = Math.min(1, this._correctPlacements / Math.max(minMoves, 1));
    this.levelMetrics.push({
      timeUsed: this._phase2Time, masteryIndex, minMoves, totalMoves,
      invalidClicks: this._invalidClicks, backtracks: this._backtracks, errors: this._errors,
      correctPlacements: this._correctPlacements
    });

    const payload = this._buildSessionPayload(completedLevel);

    const results = {
      gameName: 'puzzle',
      accuracy: this._correctPlacements / Math.max(this.currentTemplates.length, 1),
      level: completedLevel,
      payload:  payload,
      displayMetrics: [
        { label: '🧩 Pièces correctes',  value: `${this._correctPlacements} / ${this.currentTemplates.length}` },
        { label: '🏆 Score total',        value: this.score },
        { label: '📊 Niveau',             value: completedLevel },
        { label: '⏱ Durée session',      value: `${((Date.now() - this.sessStartMs) / 1000).toFixed(0)} s` },
      ],
    };
    setTimeout(() => this.core.onGameComplete(results), 1500);
  }

  _buildSessionPayload(maxLevelReached) {
    const n = Math.max(this.levelMetrics.length, 1);
    
    let totalTotalMoves = 0, totalCorrect = 0, totalErrors = 0;
    let sumMastery = 0, sumPlanning = 0, sumImpulsivity = 0, sumAttention = 0, sumFrustration = 0, sumTime = 0;

    for (const m of this.levelMetrics) {
      totalTotalMoves += m.totalMoves;
      totalCorrect += m.correctPlacements;
      totalErrors += m.errors;
      sumMastery += m.masteryIndex;
      sumPlanning += (m.minMoves / Math.max(m.totalMoves, 1));
      sumImpulsivity += Math.min(1, m.invalidClicks / Math.max(m.totalMoves, 1));
      sumAttention += Math.max(0, 1 - m.backtracks / Math.max(m.totalMoves, 1));
      sumFrustration += Math.min(1, m.errors * 2 / Math.max(m.minMoves, 1));
      sumTime += m.timeUsed >= 0 ? Math.max(0, 1 - m.timeUsed / 30) : 0;
    }
    const totalIncorrect = Math.max(totalErrors, totalTotalMoves - totalCorrect);

    const base = NeuroAPI.buildBase(this.player, {
      duration_s: Math.round((Date.now() - this.sessStartMs) / 1000 * 10) / 10,
      totalActions: totalTotalMoves,
      correct: totalCorrect,
      incorrect: totalIncorrect,
      completed: !this.gameOver,  // true when quitting voluntarily, false on timeout
      hintUsage: 0,
      level: maxLevelReached,
    });
    
    return {
      ...base,
      max_level_reached:     maxLevelReached,
      levels_completed:      this.levelMetrics.length,
      avg_mastery_index:     +(sumMastery / n).toFixed(4),
      avg_planning_score:    +(sumPlanning / n).toFixed(4),
      avg_impulsivity_score: +(sumImpulsivity / n).toFixed(4),
      avg_attention_score:   +(sumAttention / n).toFixed(4),
      avg_frustration_score: +(sumFrustration / n).toFixed(4),
      avg_time_score:        +(sumTime / n).toFixed(4),
    };
  }

  _resetMetrics() {
    this._movesUsed = 0; this._backtracks = 0; this._errors = 0;
    this._invalidClicks = 0; this._rotations = 0;
  }

  /** Called by GameCanvas.handleBackToMenu when quitting mid-session. */
  getPartialResults() {
    // If game is already over (timeout triggered onGameComplete) or no levels played, skip
    if (this.gameOver || this.levelMetrics.length === 0) return null;

    const completedLevel = this.diffState.levelIdx + 1;
    const payload = this._buildSessionPayload(completedLevel);
    payload.session_outcome = 'interrupted';
    payload.Game_Completion_Status = 'Not Completed';

    const totalCorrect = this.levelMetrics.reduce((s, m) => s + m.correctPlacements, 0);
    const totalPieces = this.levelMetrics.reduce((s, m) => s + m.minMoves, 0);
    const accuracy = totalCorrect / Math.max(totalPieces, 1);

    return {
      gameName: 'puzzle',
      accuracy,
      level: completedLevel,
      payload,
      displayMetrics: [],
    };
  }

  // ── Mastery streak ──────────────────────────────────────────────────────────

  _getStreak(lvl) {
    const h = this._sessHistory[lvl] || [];
    let s = 0;
    for (let i = h.length - 1; i >= 0; i--) { if (h[i] >= 0.75) s++; else break; }
    return s;
  }
  _updateStreak(lvl, mi) {
    if (!this._sessHistory[lvl]) this._sessHistory[lvl] = [];
    this._sessHistory[lvl] = [...this._sessHistory[lvl].slice(-4), +mi.toFixed(3)];
    // Removed localStorage.setItem per user request so levels reset
  }

  // ── Input ──────────────────────────────────────────────────────────────────

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return {
      px: (e.touches ? e.touches[0].clientX : e.clientX) - r.left,
      py: (e.touches ? e.touches[0].clientY : e.clientY) - r.top,
    };
  }

  _pointerDown(e) {
    if (this.gameOver || this.successPause || !this.layout) return;
    e.preventDefault();

    // Dismiss tutorial banner on any click and restart the phase timer
    if (this._tutorialBanner) {
      this._tutorialBanner = null;
      this.phaseStartMs = Date.now();
      return;
    }

    const { px, py } = this._pos(e);
    const { gridOX, gridOY, cs } = this.layout;

    if (this.phase === 1) {
      this.currentTemplates.forEach((tp, idx) => {
        if (this.scannedIdx.has(idx)) return;
        for (const [c, r] of tp.cells) {
          const rx = gridOX + (tp.targetX + c) * cs;
          const ry = gridOY + (tp.targetY + r) * cs;
          if (px >= rx && px < rx + cs && py >= ry && py < ry + cs) {
            this.scannedIdx.add(idx);
            this.scanGlow[idx] = 1.0;
            break;
          }
        }
      });
      return;
    }

    // Phase 2: drag
    let hit = false;
    for (let i = this.playerPieces.length - 1; i >= 0; i--) {
      const p = this.playerPieces[i];
      if (this._pieceHit(p, px, py)) {
        hit = true;
        const ox = p.state === 'grid' ? gridOX + p.gridX * cs : p.pixelX;
        const oy = p.state === 'grid' ? gridOY + p.gridY * cs : p.pixelY;
        p.pixelX = ox; p.pixelY = oy;
        this.dragOffX = ox - px; this.dragOffY = oy - py;
        p.dragging = true; p.state = 'floating'; p.dragCount++;
        this.draggingPiece = p; this._movesUsed++;
        this.playerPieces.splice(i, 1); this.playerPieces.push(p);
        break;
      }
    }
    if (!hit) this._invalidClicks++;
  }

  _pointerMove(e) {
    if (!this.draggingPiece) return;
    const { px, py } = this._pos(e);
    this.draggingPiece.pixelX = px + this.dragOffX;
    this.draggingPiece.pixelY = py + this.dragOffY;
  }

  _pointerUp(e) {
    if (!this.draggingPiece || !this.layout) return;
    const p = this.draggingPiece;
    this.draggingPiece = null; p.dragging = false;

    const { gridOX, gridOY, cols, rows, cs } = this.layout;
    const { px, py } = this._pos(e);

    const inGrid = px >= gridOX && py >= gridOY &&
                   px <  gridOX + cols * cs &&
                   py <  gridOY + rows * cs;

    if (inGrid) {
      const gx = Math.round((p.pixelX - gridOX) / cs);
      const gy = Math.round((p.pixelY - gridOY) / cs);
      const newCells = p.cells.map(([c, r]) => [gx + c, gy + r]);
      const oob = newCells.some(([c, r]) => c < 0 || c >= cols || r < 0 || r >= rows);
      const overlap = newCells.some(([c, r]) =>
        this.playerPieces.some(op => op !== p && op.state === 'grid' &&
          op.cells.some(([oc, or]) => op.gridX + oc === c && op.gridY + or === r))
      );

      if (oob || overlap) {
        this._errors++; this._backtracks++;
        this.core.showFeedback(false);
        p.state = 'inventory'; p.placed = false;
        // Return to original inventory position
        this._placeInventory();
      } else {
        p.gridX = gx; p.gridY = gy; p.state = 'grid';
        const correctPlacement = !p.isDistractor && this._isPieceCorrect(p);
        if (correctPlacement) {
          if (!p.placed) this._correctPlacements++;
          p.placed = true; this.glowTimers[p.id] = 40;
          this.floatingPraise = {
            text: this._praise[Math.floor(Math.random() * this._praise.length)],
            expireMs: Date.now() + 1500,
          };
          this.core.showFeedback(true);
          this.core.setScore(this.score + this._correctPlacements * 50);
        } else {
          p.placed = false;
          this._errors++;
          this.core.showFeedback(false);
        }
        if (this._isVictory()) this._winLevel();
      }
    } else {
      p.state = 'inventory'; p.placed = false; this._backtracks++;
    }
  }

  _rightClick(e) {
    if (this.phase !== 2 || this.gameOver || !this.layout) return;
    const { px, py } = this._pos(e);
    for (let i = this.playerPieces.length - 1; i >= 0; i--) {
      const p = this.playerPieces[i];
      if (this._pieceHit(p, px, py)) {
        p.cells = rotateCells90(p.cells); this._rotations++;
        if (this._isVictory()) this._winLevel();
        break;
      }
    }
  }

  _pieceHit(p, px, py) {
    const { gridOX, gridOY, cs } = this.layout;
    const ox = p.state === 'grid' ? gridOX + p.gridX * cs : p.pixelX;
    const oy = p.state === 'grid' ? gridOY + p.gridY * cs : p.pixelY;
    return p.cells.some(([c, r]) =>
      px >= ox + c * cs && px < ox + c * cs + cs &&
      py >= oy + r * cs && py < oy + r * cs + cs
    );
  }

  _isPieceCorrect(p) {
    const tp = this.currentTemplates.find((t, i) => i === p.id);
    if (!tp) return false;
    const tSet = new Set(tp.cells.map(([c,r]) => `${tp.targetX+c},${tp.targetY+r}`));
    const pSet = new Set(p.cells.map(([c,r]) => `${p.gridX+c},${p.gridY+r}`));
    if (tSet.size !== pSet.size) return false;
    for (const k of tSet) if (!pSet.has(k)) return false;
    return true;
  }

  _isVictory() {
    return this.playerPieces.filter(p => !p.isDistractor).every(p => p.state === 'grid' && this._isPieceCorrect(p));
  }

  _winLevel() {
    if (this.successPause) return;
    this.successPause    = true;
    this.successPauseMs  = Date.now();
    this.message  = this.core.t('engine.correct');
    this.messageMs = Date.now();
    this.core.spawnParticles();
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  _draw() {
    if (!this.layout) return;
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;

    const { gridOX, gridOY, gridZoneX, gridZoneY, gridZoneW, gridZoneH,
            invY, invH, cols, rows, cs } = this.layout;
    const HEADER = 58;

    // Background (mirrors BG_COLOR = (245,247,250))
    ctx.fillStyle = '#f5f7fa'; ctx.fillRect(0, 0, W, H);

    // Grid zone background (TARGET_BG or PLANK_BG)
    ctx.fillStyle = this.phase === 1 ? '#ebf2fa' : '#faf8f0';
    this._rr(ctx, gridZoneX, gridZoneY, gridZoneW, gridZoneH, 15); ctx.fill();
    ctx.strokeStyle = '#c8ccd8'; ctx.lineWidth = 2;
    this._rr(ctx, gridZoneX, gridZoneY, gridZoneW, gridZoneH, 15); ctx.stroke();

    // Inventory zone background (INVENTORY_BG = (240,240,240))
    ctx.fillStyle = '#f0f0f0';
    this._rr(ctx, gridZoneX, invY, gridZoneW, invH, 15); ctx.fill();
    ctx.strokeStyle = '#c8ccd8'; ctx.lineWidth = 2;
    this._rr(ctx, gridZoneX, invY, gridZoneW, invH, 15); ctx.stroke();

    // Header (white bar)
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, W, HEADER);
    ctx.strokeStyle = '#e0e2ea'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, HEADER); ctx.lineTo(W, HEADER); ctx.stroke();

    // Header content
    ctx.font = 'bold 20px Nunito, sans-serif';
    ctx.fillStyle = '#28283a'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(this.core.t('engine.puzzleTitle', { level: this.diffState.levelIdx + 1 }), 16, HEADER / 2);

    // Streak dots
    for (let i = 0; i < 3; i++) {
      ctx.beginPath(); ctx.arc(16 + i * 18, HEADER - 9, 5, 0, Math.PI * 2);
      ctx.fillStyle = i < this._masteryStreak ? '#50c878' : '#c8ccd4'; ctx.fill();
    }
    ctx.font = '13px Nunito'; ctx.fillStyle = '#8c92a0'; ctx.textAlign = 'left';
    ctx.fillText(`MI ${this._masteryStreak}/3`, 74, HEADER - 9);

    ctx.fillStyle = '#50c878'; ctx.textAlign = 'right';
    ctx.font = 'bold 20px Nunito';
    ctx.fillText(this.core.t('engine.score', { score: this.score }), W - 16, HEADER / 2);

    // Timer countdown ring (replaces raw number)
    const elapsed = (Date.now() - this.phaseStartMs) / 1000;
    const total = this.phase === 1 ? this._phase1Time : this._phase2Time;
    const timeLeft = Math.max(0, Math.round(total - elapsed));
    const timePct = Math.max(0, (total - elapsed) / total);
    ctx.font = 'bold 16px Nunito'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = this.phase === 1 ? '#c43a3a' : '#3a5caa';
    ctx.fillText(this.phase === 1 ? this.core.t('engine.phaseScan') : this.core.t('engine.phaseBuild'),
                 W / 2, 18);
    // Countdown ring
    const ringX = W / 2, ringY = 38, ringR = 14;
    ctx.beginPath();
    ctx.arc(ringX - 30, ringY, ringR, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(100,100,120,0.15)'; ctx.lineWidth = 3; ctx.stroke();
    ctx.beginPath();
    ctx.arc(ringX - 30, ringY, ringR, -Math.PI / 2, -Math.PI / 2 + timePct * Math.PI * 2);
    ctx.strokeStyle = timeLeft <= 5 ? '#e65a5a' : (this.phase === 1 ? '#c43a3a' : '#3a5caa');
    ctx.lineWidth = 3; ctx.stroke();
    ctx.font = 'bold 18px Nunito';
    ctx.fillStyle = timeLeft <= 5 ? '#e65a5a' : '#28283a';
    ctx.fillText(`${timeLeft}s`, ringX + 6, ringY);

    // Zone labels
    ctx.font = 'bold 14px Nunito'; ctx.textBaseline = 'top';
    ctx.fillStyle = this.phase === 1 ? '#a03030' : '#3a5a8a';
    ctx.textAlign = 'left';
    ctx.fillText(this.phase === 1 ? this.core.t('engine.scanPieces') : this.core.t('engine.build'), gridZoneX + 15, gridZoneY + 6);
    if (this.phase === 1) {
      const scanProg = `${this.scannedIdx.size}/${this.currentTemplates.length}`;
      ctx.fillStyle = '#3a803a'; ctx.textAlign = 'right';
      ctx.fillText(scanProg, gridZoneX + gridZoneW - 15, gridZoneY + 6);
    }
    ctx.fillStyle = '#505060'; ctx.textAlign = 'left';
    ctx.fillText(this.phase === 1 ? this.core.t('engine.inventory') : this.core.t('engine.rotateHint'), gridZoneX + 15, invY + 6);

    // Grid cell outlines
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        ctx.strokeStyle = '#dde0eb'; ctx.lineWidth = 1;
        ctx.strokeRect(gridOX + c * cs + 0.5, gridOY + r * cs + 0.5, cs, cs);
      }
    }

    // Phase content
    if (this.phase === 1) this._drawScanPhase(ctx);
    else                   this._drawBuildPhase(ctx);

    // Floating praise
    if (this.floatingPraise && Date.now() < this.floatingPraise.expireMs) {
      const a = (this.floatingPraise.expireMs - Date.now()) / 1500;
      ctx.save(); ctx.globalAlpha = a;
      ctx.font = 'bold 22px Nunito'; ctx.fillStyle = '#50c878';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.floatingPraise.text, gridOX + cols * cs / 2, gridOY - 14);
      ctx.restore();
    } else this.floatingPraise = null;

    // Tutorial banner (centered card overlay)
    if (this._tutorialBanner) {
      // Subtle backdrop
      ctx.save();
      ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
      ctx.fillRect(0, 0, W, H);

      // Card dimensions
      const cardW = Math.min(420, W - 60);
      const lines = this._tutorialBanner.split('\n');
      const cardH = 70 + lines.length * 36;
      const cardX = (W - cardW) / 2;
      const cardY = (H - cardH) / 2;

      // Card background + border
      ctx.fillStyle = '#1e2140';
      ctx.beginPath();
      roundRect(ctx, cardX, cardY, cardW, cardH, 16);
      ctx.fill();
      ctx.strokeStyle = '#ffd166';
      ctx.lineWidth = 2;
      ctx.beginPath();
      roundRect(ctx, cardX, cardY, cardW, cardH, 16);
      ctx.stroke();

      // Title (first line)
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = 'bold 20px Nunito';
      ctx.fillStyle = '#ffd166';
      ctx.fillText(lines[0], W / 2, cardY + 32);
      if (lines.length > 1) {
        ctx.font = '16px Nunito';
        ctx.fillStyle = '#e0e0f0';
        ctx.fillText(lines[1], W / 2, cardY + 68);
      }

      // Click prompt
      const blink = Math.sin(Date.now() / 500) * 0.25 + 0.75;
      ctx.globalAlpha = blink;
      ctx.font = '13px Nunito';
      ctx.fillStyle = '#a0a0b8';
      ctx.fillText(this.core.t('engine.clickToContinue'), W / 2, cardY + cardH - 18);
      ctx.restore();
      return;
    }


    // Central message (mirrors pygame overlay)
    if (Date.now() - this.messageMs < 3500 || this.successPause || this.gameOver) {
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,0.70)';
      ctx.fillRect(0, H / 2 - 28, W, 56);
      ctx.font = 'bold 22px Nunito'; ctx.fillStyle = '#fff';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.message, W / 2, H / 2);
      ctx.restore();
    }
  }

  _drawScanPhase(ctx) {
    const { gridOX, gridOY, cs } = this.layout;
    const now = Date.now();

    this.currentTemplates.forEach((tp, idx) => {
      const scanned = this.scannedIdx.has(idx);
      const glow    = this.scanGlow[idx] || 0;
      const color   = scanned ? tp.color : this._lightenHex(tp.color, 0.45);

      tp.cells.forEach(([c, r]) => {
        const rx = gridOX + (tp.targetX + c) * cs;
        const ry = gridOY + (tp.targetY + r) * cs;
        ctx.fillStyle = color;
        ctx.fillRect(rx + 1, ry + 1, cs - 2, cs - 2);
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
        ctx.strokeRect(rx + 1, ry + 1, cs - 2, cs - 2);

        if (glow > 0) {
          ctx.save(); ctx.globalAlpha = glow * 0.55;
          ctx.strokeStyle = '#64ff80'; ctx.lineWidth = 4;
          ctx.strokeRect(rx - 1, ry - 1, cs + 2, cs + 2);
          ctx.restore();
        }
      });

      if (scanned) {
        const fc = tp.cells[0];
        ctx.font = 'bold 18px Nunito'; ctx.fillStyle = '#20b840';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('✓', gridOX + (tp.targetX + fc[0]) * cs + cs / 2,
                         gridOY + (tp.targetY + fc[1]) * cs + cs / 2);
      }
      if (glow > 0) this.scanGlow[idx] = Math.max(0, glow - 0.035);
    });

    // Distractor templates (shown dashed, with "?" badge)
    this.distractorTemplates.forEach(tp => {
      tp.cells.forEach(([c, r]) => {
        const rx = gridOX + (tp.targetX + c) * cs;
        const ry = gridOY + (tp.targetY + r) * cs;
        ctx.fillStyle = tp.color;
        ctx.save(); ctx.globalAlpha = 0.6; ctx.fillRect(rx+1,ry+1,cs-2,cs-2); ctx.restore();
        // Dashed border
        ctx.setLineDash([4,4]); ctx.strokeStyle = '#505050'; ctx.lineWidth = 2;
        ctx.strokeRect(rx+1,ry+1,cs-2,cs-2); ctx.setLineDash([]);
      });
      const fc = tp.cells[0];
      ctx.font = 'bold 14px Nunito'; ctx.fillStyle = '#404040';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('?', gridOX+(tp.targetX+fc[0])*cs+cs/2, gridOY+(tp.targetY+fc[1])*cs+cs/2);
    });

    // Inventory: show pieces in their placed positions (phase-1 preview) with PULSING for unscanned
    const pulseFactor = 0.3 + 0.2 * Math.sin(Date.now() * 0.004);
    this.playerPieces.forEach((p, i) => {
      const isScanned = this.scannedIdx.has(p.id);
      this._drawPieceAt(ctx, p.pixelX, p.pixelY, p.cells, p.color, isScanned ? 0.5 : pulseFactor);
    });
  }

  _drawBuildPhase(ctx) {
    const { gridOX, gridOY, cols, rows, cs } = this.layout;

    // Hint ghost outlines
    this.currentTemplates.slice(0, this.hintsRevealed).forEach(tp => {
      tp.cells.forEach(([c, r]) => {
        const rx = gridOX + (tp.targetX + c) * cs;
        const ry = gridOY + (tp.targetY + r) * cs;
        ctx.save(); ctx.globalAlpha = 0.3; ctx.fillStyle = tp.color;
        ctx.fillRect(rx+1,ry+1,cs-2,cs-2); ctx.restore();
        ctx.save(); ctx.globalAlpha = 0.55; ctx.strokeStyle = tp.color;
        ctx.lineWidth = 2; ctx.strokeRect(rx+1,ry+1,cs-2,cs-2); ctx.restore();
      });
    });

    // Draw non-dragging pieces
    this.playerPieces.filter(p => !p.dragging).forEach(p => this._drawPiece(ctx, p));
    if (this.draggingPiece) {
      // Snap preview ghost
      const dp = this.draggingPiece;
      const { gridOX, gridOY, cols, rows, cs } = this.layout;
      const gx = Math.round((dp.pixelX - gridOX) / cs);
      const gy = Math.round((dp.pixelY - gridOY) / cs);
      const newCells = dp.cells.map(([c, r]) => [gx + c, gy + r]);
      const inGrid = dp.pixelX >= gridOX && dp.pixelY >= gridOY &&
                     dp.pixelX < gridOX + cols * cs && dp.pixelY < gridOY + rows * cs;
      if (inGrid) {
        const oob = newCells.some(([c, r]) => c < 0 || c >= cols || r < 0 || r >= rows);
        const ghostColor = oob ? 'rgba(239,68,68,0.2)' : 'rgba(34,197,94,0.2)';
        const ghostBorder = oob ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)';
        if (!oob) {
          newCells.forEach(([c, r]) => {
            const rx = gridOX + c * cs, ry = gridOY + r * cs;
            ctx.fillStyle = ghostColor;
            ctx.fillRect(rx + 1, ry + 1, cs - 2, cs - 2);
            ctx.strokeStyle = ghostBorder; ctx.lineWidth = 2;
            ctx.strokeRect(rx + 1, ry + 1, cs - 2, cs - 2);
          });
        }
      }
      this._drawPiece(ctx, this.draggingPiece, true);
    }
  }

  _drawPiece(ctx, p, isDragging = false) {
    const { gridOX, gridOY, cs } = this.layout;
    const ox = p.state === 'grid' ? gridOX + p.gridX * cs : p.pixelX;
    const oy = p.state === 'grid' ? gridOY + p.gridY * cs : p.pixelY;

    const gt = this.glowTimers[p.id] || 0;

    ctx.save();
    if (isDragging)   { ctx.shadowColor = p.color; ctx.shadowBlur = 18; }
    if (gt > 0)       { ctx.shadowColor = '#50c878'; ctx.shadowBlur = 22; this.glowTimers[p.id] = gt - 1; }

    this._drawPieceAt(ctx, ox, oy, p.cells, p.color, 1);
    ctx.restore();

    // Distractor "?" badge
    if (p.isDistractor) {
      const fc = p.cells[0];
      ctx.font = 'bold 14px Nunito'; ctx.fillStyle = '#303030';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('?', ox + fc[0]*cs + cs/2, oy + fc[1]*cs + cs/2);
    }
  }

  _drawPieceAt(ctx, ox, oy, cells, color, alpha) {
    const { cs } = this.layout;
    ctx.save(); ctx.globalAlpha = alpha;
    cells.forEach(([c, r]) => {
      const rx = ox + c * cs, ry = oy + r * cs;
      ctx.fillStyle = color; ctx.fillRect(rx+1, ry+1, cs-2, cs-2);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
      ctx.strokeRect(rx+1, ry+1, cs-2, cs-2);
    });
    ctx.restore();
  }

  _rr(ctx, x, y, w, h, r) { roundRect(ctx, x, y, w, h, r); }
  _lightenHex(hex, amt) { return lightenHex(hex, amt); }

}
