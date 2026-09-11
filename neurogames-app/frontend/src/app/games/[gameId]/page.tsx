"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import GameCanvas from "@/components/GameCanvas";
import type { CoreCallbacks, Player, GameInstance } from "@/engine/types";
import { useI18n } from "@/i18n/I18nProvider";
import { GameInfo, NeuroAPI } from "@/lib/api";
import { CONFIG } from "@/lib/config";
import { HistoryManager } from "@/lib/history";

const GAME_META: Record<string, {
  color: string;
  icon: string;
}> = {
  gonogo: {
    color: "#6C5CE7",
    icon: "🎯",
  },
  memory: {
    color: "#00B894",
    icon: "🃏",
  },
  tracking: {
    color: "#FDCB6E",
    icon: "👁️",
  },
  shapes: {
    color: "#E17055",
    icon: "🔷",
  },
  puzzle: {
    color: "#0984E3",
    icon: "🧩",
  },
};

function CustomGameRunner({ gameId }: { gameId: string }) {
  const [game, setGame] = useState<GameInfo | null>(null);
  const [status, setStatus] = useState("");

  const saveCustomHistory = useCallback((payload: Record<string, unknown>, currentGame: GameInfo) => {
    const correct = Number(payload.Correct_Responses || 0);
    const totalActions = Number(payload.Total_Actions || 0);
    const accuracy = totalActions > 0 ? correct / totalActions : 0;
    const stars = accuracy >= 0.75 ? "â­â­â­" : accuracy >= 0.45 ? "â­â­" : "â­";
    HistoryManager.push({
      id: `${gameId}_${String(payload.Game_Session_ID || Date.now())}`,
      game: gameId,
      gameName: currentGame.name || gameId,
      icon: currentGame.icon || "ðŸŽ®",
      timestamp: Date.now(),
      accuracy,
      stars,
      level: Number(payload.max_level_reached || payload.level || 1),
      duration_s: Number(payload.Time_Spent || 0),
      correct,
      totalActions,
      reactionTime: Number(payload.Reaction_Time || 0),
      performanceLevel: String(payload.Performance_Level || ""),
      displayMetrics: [
        { label: "Correct / Total", value: `${correct} / ${totalActions}` },
        { label: "PrÃ©cision", value: `${Math.round(accuracy * 100)}%` },
        { label: "Temps rÃ©action", value: `${Math.round(Number(payload.Reaction_Time || 0) * 1000)}ms` },
        { label: "Rounds jouÃ©s", value: Number(payload.rounds_played || 0) },
      ],
    });
  }, [gameId]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(async () => {
      const result = await NeuroAPI.getGames();
      if (cancelled) return;
      setGame(result.games.find((item) => item.id === gameId) || null);
    });
    return () => {
      cancelled = true;
    };
  }, [gameId]);

  useEffect(() => {
    if (!game?.entry_url) return;
    let expectedOrigin = "";
    try {
      expectedOrigin = new URL(CONFIG.API_BASE_URL).origin;
    } catch {
      expectedOrigin = "";
    }

    function handlePackageMessage(event: MessageEvent) {
      if (expectedOrigin && event.origin !== expectedOrigin) return;
      const message = event.data as { type?: string; payload?: unknown } | null;
      if (!message || message.type !== "neurogames:session") return;
      if (!message.payload || typeof message.payload !== "object" || Array.isArray(message.payload)) {
        setStatus("Game package sent an invalid telemetry message.");
        return;
      }
      setStatus("Submitting package telemetry...");
      const payload = message.payload as Record<string, unknown>;
      const currentGame = game;
      if (!currentGame) return;
      void NeuroAPI.uploadSession(gameId, payload).then((result) => {
        if (result.ok) {
          saveCustomHistory(payload, currentGame);
          setStatus("Session saved from package telemetry.");
        } else {
          setStatus(result.message);
        }
      });
    }

    window.addEventListener("message", handlePackageMessage);
    return () => window.removeEventListener("message", handlePackageMessage);
  }, [game, game?.entry_url, gameId, saveCustomHistory]);

  async function submitDemoSession() {
    setStatus("Submitting demo telemetry...");
    let player = { id: "demo_player", ageGroup: "9-11", cognitiveLevel: "Medium" };
    try {
      const raw = localStorage.getItem("neuro_player");
      if (raw) {
        const parsed = JSON.parse(raw);
        player = {
          id: parsed.id || "demo_player",
          ageGroup: parsed.ageGroup || "9-11",
          cognitiveLevel: parsed.cognitiveLevel || "Medium",
        };
      }
    } catch {
      // Keep fallback player.
    }
    const payload = NeuroAPI.buildBase(player, {
      duration_s: 45,
      totalActions: 20,
      correct: 15,
      incorrect: 5,
      avgRT_s: 0.82,
      completed: true,
      roundsPlayed: 1,
      roundsPassed: 1,
      sessionOutcome: "completed",
      level: 1,
    });
    const result = await NeuroAPI.uploadSession(gameId, payload);
    if (result.ok && game) {
      saveCustomHistory(payload, game);
      setStatus("Demo session saved. This module is ready for the real sandbox package.");
    } else {
      setStatus(result.message);
    }
  }

  if (!game) {
    return (
      <div style={{ textAlign: "center", padding: "4rem", color: "#7C3AED" }}>
        <h1>Approved Game Module</h1>
        <p>Loading module metadata...</p>
        <Link href="/games" className="btn btn-secondary">Back to games</Link>
      </div>
    );
  }

  if (game.entry_url) {
    const frameSrc = game.entry_url.startsWith("http")
      ? game.entry_url
      : `${CONFIG.API_BASE_URL}${game.entry_url}`;
    return (
      <div style={{ minHeight: "100vh", background: "#0F172A", color: "#E2E8F0", display: "flex", flexDirection: "column" }}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "14px 18px", borderBottom: "1px solid rgba(148,163,184,0.2)" }}>
          <div>
            <strong>{game.icon} {game.name}</strong>
            <span style={{ marginLeft: 12, color: "#94A3B8", fontSize: 13 }}>{game.cognitive_domain}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {status && <span style={{ color: "#CBD5E1", fontSize: 13, fontWeight: 700 }}>{status}</span>}
            <Link href="/games" className="btn btn-secondary">Back to games</Link>
          </div>
        </header>
        <iframe
          title={game.name}
          src={frameSrc}
          sandbox="allow-scripts allow-same-origin"
          style={{ flex: 1, width: "100%", border: 0, background: "#fff" }}
        />
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "2rem", background: "#EEF2FF" }}>
      <div style={{ maxWidth: 620, background: "#fff", border: "2px solid #C4B5FD", borderRadius: 16, padding: 28, textAlign: "center", boxShadow: "0 18px 40px rgba(109,40,217,0.16)" }}>
        <div style={{ fontSize: 64, marginBottom: 10 }}>{game.icon}</div>
        <h1 style={{ color: "#4C1D95", margin: 0 }}>{game.name}</h1>
        <p style={{ color: "#6D28D9", fontWeight: 800 }}>{game.cognitive_domain}</p>
        <p style={{ color: "#475569", lineHeight: 1.6 }}>
          This developer module has been accepted and published by the superadmin.
          It is registered in NeuroGames and accepts telemetry. If the request included a valid ZIP package,
          the package is loaded here; otherwise this demo button submits contract-compatible telemetry.
        </p>
        <button className="btn btn-primary btn-large" onClick={submitDemoSession}>
          Play demo telemetry round
        </button>
        {status && <p style={{ color: "#475569", fontWeight: 700 }}>{status}</p>}
        <p style={{ marginTop: 20 }}>
          <Link href="/games" className="btn btn-secondary">Back to games</Link>
        </p>
      </div>
    </div>
  );
}

// Dynamic import factories for each game
function createGameFactory(gameId: string) {
  return async (
    canvas: HTMLCanvasElement,
    player: Player,
    core: CoreCallbacks,
  ): Promise<GameInstance> => {
    switch (gameId) {
      case "gonogo": {
        const { GoNoGoGame } = await import("@/engine/games/GoNoGoGame");
        return new GoNoGoGame(canvas, player, core);
      }
      case "memory": {
        const { MemoryGame } = await import("@/engine/games/MemoryGame");
        return new MemoryGame(canvas, player, core);
      }
      case "tracking": {
        const { TrackingGame } = await import("@/engine/games/TrackingGame");
        return new TrackingGame(canvas, player, core);
      }
      case "shapes": {
        const { ShapesGame } = await import("@/engine/games/ShapesGame");
        return new ShapesGame(canvas, player, core);
      }
      case "puzzle": {
        const { PuzzleGame } = await import("@/engine/games/PuzzleGame");
        return new PuzzleGame(canvas, player, core);
      }
      default:
        throw new Error(`Unknown game: ${gameId}`);
    }
  };
}

export default function GamePlayPage() {
  const { t } = useI18n();
  const params = useParams();
  const gameId = params.gameId as string;

  const meta = GAME_META[gameId];
  if (!meta) {
    return <CustomGameRunner gameId={gameId} />;
  }

  // Synchronous wrapper that starts the async game load
  const createGame = (
    canvas: HTMLCanvasElement,
    player: Player,
    core: CoreCallbacks,
  ): GameInstance => {
    const factory = createGameFactory(gameId);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const proxy: any = {
      start() {
        factory(canvas, player, core).then(game => {
          // Copy ALL methods (own + prototype) so that _startRound,
          // pause, resume etc. are directly available on the proxy.
          // IMPORTANT: use getOwnPropertyDescriptor to avoid triggering
          // getters (e.g. PuzzleGame._phase1Time) which would crash
          // because `this` context is wrong during enumeration.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          let proto = game as any;
          while (proto && proto !== Object.prototype) {
            for (const key of Object.getOwnPropertyNames(proto)) {
              if (key === 'constructor') continue;
              const desc = Object.getOwnPropertyDescriptor(proto, key);
              // Skip getters/setters — they rely on instance state
              if (desc && (desc.get || desc.set)) continue;
              if (typeof desc?.value === 'function') {
                proxy[key] = desc.value.bind(game);
              }
            }
            proto = Object.getPrototypeOf(proto);
          }
          // Also copy non-function own properties (for completeness)
          Object.assign(proxy, game);
          game.start();
        });
      },
      stop() {},
      onResize() {},
    };
    return proxy as GameInstance;
  };

  return (
    <GameCanvas
      createGame={createGame}
      gameName={t(`games.${gameId}.name`)}
      gameColor={meta.color}
      gameIcon={meta.icon}
      gameInstruction={t(`games.${gameId}.instruction`)}
      gameKey={gameId}
    />
  );
}
