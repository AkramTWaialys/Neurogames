"use client";

import { useRef, useEffect, useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import type { CoreCallbacks, GameResults, RoundPassedInfo, GameInstance, Player } from "@/engine/types";
import { SessionEngine } from "@/lib/sessionEngine";
import { DifficultyProfile } from "@/engine/DifficultyProfile";
import { AudioManager } from "@/engine/AudioManager";
import { useI18n } from "@/i18n/I18nProvider";
import { translateMetricLabel } from "@/i18n/labels";
import styles from "./GameCanvas.module.css";

interface GameCanvasProps {
  createGame: (canvas: HTMLCanvasElement, player: Player, core: CoreCallbacks) => GameInstance;
  gameName: string;
  gameColor: string;
  gameIcon: string;
  gameInstruction: string;
  gameKey: string;
}

function getEncouragement(acc: number, t: (key: string) => string) {
  if (acc >= 0.75) {
    const msgs = [
      { emoji: "🌟", msg: t("encourage.high.1.msg"), detail: t("encourage.high.1.detail") },
      { emoji: "🔥", msg: t("encourage.high.2.msg"), detail: t("encourage.high.2.detail") },
      { emoji: "🎉", msg: t("encourage.high.3.msg"), detail: t("encourage.high.3.detail") },
    ];
    return msgs[Math.floor(Math.random() * msgs.length)];
  }
  if (acc >= 0.45) {
    const msgs = [
      { emoji: "💪", msg: t("encourage.mid.1.msg"), detail: t("encourage.mid.1.detail") },
      { emoji: "🚀", msg: t("encourage.mid.2.msg"), detail: t("encourage.mid.2.detail") },
      { emoji: "😊", msg: t("encourage.mid.3.msg"), detail: t("encourage.mid.3.detail") },
    ];
    return msgs[Math.floor(Math.random() * msgs.length)];
  }
  const msgs = [
    { emoji: "🌱", msg: t("encourage.low.1.msg"), detail: t("encourage.low.1.detail") },
    { emoji: "🎯", msg: t("encourage.low.2.msg"), detail: t("encourage.low.2.detail") },
    { emoji: "⭐", msg: t("encourage.low.3.msg"), detail: t("encourage.low.3.detail") },
  ];
  return msgs[Math.floor(Math.random() * msgs.length)];
}

export default function GameCanvas({ createGame, gameName, gameColor, gameIcon, gameInstruction, gameKey }: GameCanvasProps) {
  const i18n = useI18n();
  const { t } = i18n;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const gameRef = useRef<GameInstance | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timerSecondsRef = useRef(0);
  const router = useRouter();

  // ── Frustration (rage-click) tracker ──────────────────────────────
  const frustrationTimestamps = useRef<number[]>([]);
  const frustrationClicksRef = useRef(0);

  const [showInstructions, setShowInstructions] = useState(true);
  const [level, setLevel] = useState(1);
  const [score, setScore] = useState(0);
  const [progress, setProgress] = useState(0);
  const [timer, setTimer] = useState("0:00");
  const [feedback, setFeedback] = useState<{ correct: boolean; visible: boolean }>({ correct: false, visible: false });
  const [toast, setToast] = useState<{ msg: string; color: string; visible: boolean }>({ msg: "", color: "", visible: false });
  const [results, setResults] = useState<GameResults | null>(null);
  const [roundPause, setRoundPause] = useState<RoundPassedInfo | null>(null);
  const [roundFailed, setRoundFailed] = useState<GameResults | null>(null);
  const [uploadStatusKey, setUploadStatusKey] = useState<string>("");
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(false);
  const audioRef = useRef<AudioManager>(new AudioManager());
  const i18nRef = useRef(i18n);

  useEffect(() => {
    i18nRef.current = i18n;
  }, [i18n]);

  // Read player from localStorage (set during login)
  const getPlayer = (): Player => {
    if (typeof window === "undefined") return { id: "demo", age: 10, ageGroup: "9-11", cognitiveLevel: "Medium", adhdSubtype: null };
    try {
      const stored = JSON.parse(localStorage.getItem("neuro_player") || "null");
      if (stored) return stored;
    } catch { /* ignore */ }
    return { id: "demo", age: 10, ageGroup: "9-11", cognitiveLevel: "Medium", adhdSubtype: null };
  };

  const player = getPlayer();

  // Timer
  const startTimer = useCallback(() => {
    timerSecondsRef.current = 0;
    setTimer("0:00");
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      timerSecondsRef.current++;
      const m = Math.floor(timerSecondsRef.current / 60);
      const s = timerSecondsRef.current % 60;
      setTimer(`${m}:${s.toString().padStart(2, "0")}`);
    }, 1000);
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  }, []);

  const showFeedbackFn = useCallback((correct: boolean) => {
    setFeedback({ correct, visible: true });
    audioRef.current.play(correct ? "correct" : "wrong");
    setTimeout(() => setFeedback(f => ({ ...f, visible: false })), 800);
  }, []);

  const showToast = useCallback((msg: string, color: string) => {
    setToast({ msg, color, visible: true });
    setTimeout(() => setToast(t => ({ ...t, visible: false })), 2500);
  }, []);

  const playSoundFn = useCallback((name: string) => {
    audioRef.current.play(name as import("@/engine/AudioManager").SoundName);
  }, []);

  const handlePause = useCallback(() => {
    setPaused(p => {
      const next = !p;
      audioRef.current.play("pause");
      if (next) {
        gameRef.current?.pause?.();
        if (timerRef.current) clearInterval(timerRef.current);
      } else {
        gameRef.current?.resume?.();
        timerRef.current = setInterval(() => {
          timerSecondsRef.current++;
          const m = Math.floor(timerSecondsRef.current / 60);
          const s = timerSecondsRef.current % 60;
          setTimer(`${m}:${s.toString().padStart(2, "0")}`);
        }, 1000);
      }
      return next;
    });
  }, []);

  const handleMute = useCallback(() => {
    const nowMuted = audioRef.current.toggleMute();
    setMuted(nowMuted);
  }, []);

  // Save session to history + upload to backend
  // Replaced saveSession with SessionEngine logic directly inside callbacks

  const coreCallbacks: CoreCallbacks = {
    showFeedback: showFeedbackFn,
    setLevel,
    setScore,
    _setProgress: setProgress,
    toast: showToast,
    spawnParticles: () => {},
    playSound: playSoundFn,
    onRoundPassed: (info: RoundPassedInfo) => { audioRef.current.play("levelup"); setRoundPause(info); },
    onRoundFailed: (r: GameResults) => { stopTimer(); setRoundFailed(r); },
    onGameComplete: async (r: GameResults) => {
      stopTimer();
      audioRef.current.play("levelup");
      setResults(r);
      const payload = r.payload || {};
      payload.frustration_clicks = (payload.frustration_clicks as number || 0) + frustrationClicksRef.current;
      SessionEngine.pushLevel(gameKey, player, payload);

      const isCompleted = payload.Game_Completion_Status === "Completed";

      if (!isCompleted) {
        setUploadStatusKey("game.upload.sending");
        const outcome = (payload.session_outcome as string) || "failed";
        await SessionEngine.commitSession(outcome);
        DifficultyProfile.reset(gameKey, player);
        setUploadStatusKey("game.upload.saved");
      }
    },
    continueNextRound: () => {},
    t: (key, params) => i18nRef.current.t(key, params),
    getLocale: () => i18nRef.current.locale,
    getDir: () => i18nRef.current.dir,
  };

  const startGame = () => {
    setShowInstructions(false);
  };

  // Setup checks for abandoned sessions
  useEffect(() => {
    SessionEngine.commitAbandonedSession();
  }, []);

  // Initialize the game AFTER the canvas is rendered
  useEffect(() => {
    if (showInstructions) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    startTimer();

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      gameRef.current?.onResize();
    };

    resize();
    window.addEventListener("resize", resize);

    // ── Frustration click detector (4 clicks within 1s) ──────────
    frustrationTimestamps.current = [];
    frustrationClicksRef.current = 0;
    const onPointerDown = () => {
      const now = Date.now();
      frustrationTimestamps.current.push(now);
      // Keep only timestamps within the last 1 second
      frustrationTimestamps.current = frustrationTimestamps.current.filter(t => now - t <= 1000);
      if (frustrationTimestamps.current.length >= 4) {
        frustrationClicksRef.current++;
        frustrationTimestamps.current = []; // reset window
      }
    };
    canvas.addEventListener("pointerdown", onPointerDown, { passive: true });

    const game = createGame(canvas, player, coreCallbacks);
    gameRef.current = game;
    game.start();

    return () => {
      game.stop();
      stopTimer();
      canvas.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("resize", resize);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInstructions]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      gameRef.current?.stop();
      stopTimer();
    };
  }, [stopTimer]);

  const handleContinueRound = () => {
    setRoundPause(null);
    if (gameRef.current && gameRef.current._startRound) {
      gameRef.current._startRound();
    }
  };

  const handleRetry = () => {
    setRoundFailed(null);
    setResults(null);
    setUploadStatusKey("");
    startTimer();
    const canvas = canvasRef.current;
    if (!canvas) return;
    gameRef.current?.stop();
    const game = createGame(canvas, player, coreCallbacks);
    gameRef.current = game;
    game.start();
  };

  const handleBackToMenu = async () => {
    if (gameRef.current?.getPartialResults) {
      const partial = gameRef.current.getPartialResults();
      if (partial) {
        partial.payload.frustration_clicks = (partial.payload.frustration_clicks as number || 0) + frustrationClicksRef.current;
        SessionEngine.pushLevel(gameKey, player, partial.payload);
      }
    }
    gameRef.current?.stop();
    stopTimer();
    setUploadStatusKey("game.upload.sending");
    await SessionEngine.commitSession("completed");
    DifficultyProfile.reset(gameKey, player);
    router.push("/games");
  };

  // Compute results display data
  const acc = results?.accuracy ?? roundFailed?.accuracy ?? 0;
  const stars = acc >= 0.75 ? "⭐⭐⭐" : acc >= 0.45 ? "⭐⭐" : "⭐";
  const trophy = acc >= 0.75 ? "🏆" : acc >= 0.45 ? "🥈" : "🥉";
  const resultTitle = acc >= 0.75 ? t("game.feedback.excellent") : acc >= 0.45 ? t("game.feedback.good") : t("game.feedback.keepGoing");
  const enc = getEncouragement(acc, t);

  return (
    <div className={styles.wrapper} style={{ "--game-color": gameColor } as React.CSSProperties}>
      {/* ── Instruction Overlay ────────────────────────── */}
      {showInstructions && (
        <div className={styles.instructionOverlay}>
          <div className={styles.instructionCard}>
            <div className={styles.instructionIcon}>{gameIcon}</div>
            <h2 className={styles.instructionTitle}>{gameName}</h2>
            <p className={styles.instructionText}>{gameInstruction}</p>
            <button className={styles.startBtn} onClick={startGame}>
              {t("actions.ready")}
            </button>
            <button className={styles.backBtnSmall} onClick={handleBackToMenu}>
              {t("actions.backToMenu")}
            </button>
          </div>
        </div>
      )}

      {/* ── HUD Bar ────────────────────────────────────── */}
      {!showInstructions && (
        <>
          <div className={styles.hud}>
            <button className={styles.hudBack} onClick={handleBackToMenu}>←</button>
            <span className={styles.hudLabel}>{gameName}</span>
            <span className={styles.hudLevel}>{t("game.hud.level", { level })}</span>
            <span className={styles.hudTimer}>{timer}</span>
            <div className={styles.hudCenter}>
              <div className={styles.progressBar}>
                <div className={styles.progressFill} style={{ width: `${Math.min(progress * 100, 100)}%` }} />
              </div>
            </div>
            <div className={styles.hudRight}>
              <span className={styles.hudScore}>⭐ {score}</span>
              <button className={styles.muteBtn} onClick={handleMute} title={muted ? t("game.hud.soundOn") : t("game.hud.soundOff")}>
                {muted ? "🔇" : "🔊"}
              </button>
              <button className={styles.pauseBtn} onClick={handlePause} title={t("game.hud.pause")}>
                ⏸️
              </button>
              {/* Help tooltip */}
              <div className={styles.helpWrap}>
                <div className={styles.helpBadge}>?</div>
                <div className={styles.helpTooltip}>
                  <div className={styles.helpTooltipTitle}>{t("game.help.title", { icon: gameIcon })}</div>
                  <p className={styles.helpTooltipText}>{gameInstruction}</p>
                </div>
              </div>
            </div>
          </div>

          {/* ── Canvas ─────────────────────────────────────── */}
          <div className={styles.canvasWrap}>
            <canvas ref={canvasRef} className={styles.canvas} />
          </div>
        </>
      )}

      {/* ── Feedback Flash ─────────────────────────────── */}
      {feedback.visible && (
        <div className={`${styles.feedback} ${feedback.correct ? styles.feedbackCorrect : styles.feedbackWrong}`}>
          {feedback.correct ? "✓" : "✗"}
        </div>
      )}

      {/* ── Toast ──────────────────────────────────────── */}
      {toast.visible && (
        <div className={styles.toast} style={{ background: toast.color }}>
          {toast.msg}
        </div>
      )}

      {/* ── Pause Overlay ──────────────────────────────── */}
      {paused && (
        <div className={styles.overlay}>
          <div className={styles.pauseCard}>
            <div className={styles.pauseIcon}>⏸️</div>
            <h2>{t("game.hud.pause")}</h2>
            <p>{t("game.pause.body")}</p>
            <button className={styles.continueBtn} onClick={handlePause}>
              {t("actions.continue")}
            </button>
          </div>
        </div>
      )}

      {/* ── Round Passed Overlay ────────────────────────── */}
      {roundPause && (
        <div className={styles.overlay}>
          <div className={styles.overlayCard}>
            <h2>{roundPause.accuracy >= 0.75 ? `🌟 ${t("game.feedback.excellent")}` : roundPause.accuracy >= 0.45 ? `💪 ${t("game.feedback.good")}` : `👍 ${t("game.feedback.notBad")}`}</h2>
            <p>{t("game.roundComplete", { round: roundPause.round, max: roundPause.maxRounds })}</p>
            <div className={styles.metricGrid}>
              {roundPause.displayMetrics.map((m, i) => (
                <div key={i} className={styles.metricRow}>
                  <span>{translateMetricLabel(m.label, t)}</span>
                  <span>{m.value}</span>
                </div>
              ))}
            </div>
            <button className={styles.continueBtn} onClick={handleContinueRound}>
              {t("actions.nextRound")}
            </button>
          </div>
        </div>
      )}

      {/* ── Round Failed Overlay (POSITIVE framing) ───── */}
      {roundFailed && (
        <div className={styles.overlay}>
          <div className={styles.overlayCard}>
            <h2>💪 {resultTitle}</h2>
            <p>{t("game.failedBody", { game: gameName })}</p>
            <div className={styles.metricGrid}>
              {roundFailed.displayMetrics.map((m, i) => (
                <div key={i} className={styles.metricRow}>
                  <span>{translateMetricLabel(m.label, t)}</span>
                  <span>{m.value}</span>
                </div>
              ))}
            </div>
            <button className={styles.retryBtn} onClick={handleRetry}>
              {t("actions.retry")}
            </button>
          </div>
        </div>
      )}

      {/* ── Game Complete Overlay (Rich — matches old web_frontend) ── */}
      {results && (
        <div className={styles.overlay}>
          <div className={styles.overlayCard}>
            {/* Trophy + Title */}
            <div className={styles.resultsTrophy}>{trophy}</div>
            <h2>{resultTitle}</h2>
            <p>{t("game.completeBody", { game: gameName, level: results.level || level })}</p>
            <div className={styles.resultsStars}>{stars}</div>

            {/* Encouragement Banner */}
            <div className={styles.encourageBanner}>
              <span className={styles.encourageEmoji}>{enc.emoji}</span>
              <div>
                <div className={styles.encourageMsg}>{enc.msg}</div>
                <div className={styles.encourageDetail}>{enc.detail}</div>
              </div>
            </div>

            {/* Metrics */}
            <div className={styles.metricGrid}>
              {results.displayMetrics.map((m, i) => (
                <div key={i} className={styles.metricRow}>
                  <span>{translateMetricLabel(m.label, t)}</span>
                  <span>{m.value}</span>
                </div>
              ))}
            </div>

            {/* Actions */}
            <div className={styles.endActions}>
              <button className={styles.continueBtn} onClick={handleRetry}>
                {t("actions.nextLevel")}
              </button>
              <button className={styles.backBtnSmall} onClick={handleBackToMenu}>
                {t("actions.backToMenu")}
              </button>
            </div>

            {/* Upload status */}
            {uploadStatusKey && (
              <div className={styles.uploadStatus}>{t(uploadStatusKey)}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
