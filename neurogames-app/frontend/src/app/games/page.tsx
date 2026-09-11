"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useI18n } from "@/i18n/I18nProvider";
import { GameInfo, NeuroAPI } from "@/lib/api";
import styles from "./page.module.css";

const BUILTIN_IDS = new Set(["gonogo", "memory", "tracking", "shapes", "puzzle"]);

const FALLBACK_GAMES: GameInfo[] = [
  { id: "gonogo", name: "Go / No-Go", icon: "🎯", cognitive_domain: "Inhibitory control", description: "", difficulty_system: "", color: "#6C5CE7" },
  { id: "memory", name: "Memory Cards", icon: "🃏", cognitive_domain: "Working memory", description: "", difficulty_system: "", color: "#00B894" },
  { id: "tracking", name: "Tracking", icon: "👁️", cognitive_domain: "Sustained attention", description: "", difficulty_system: "", color: "#FDCB6E" },
  { id: "shapes", name: "Shapes", icon: "🔷", cognitive_domain: "Spatial reasoning", description: "", difficulty_system: "", color: "#E17055" },
  { id: "puzzle", name: "Puzzle", icon: "🧩", cognitive_domain: "Cognitive flexibility", description: "", difficulty_system: "", color: "#0984E3" },
];

function isBuiltin(gameId: string): boolean {
  return BUILTIN_IDS.has(gameId);
}

export default function GamesPage() {
  const { t } = useI18n();
  const [idx, setIdx] = useState(0);
  const [games, setGames] = useState<GameInfo[]>(FALLBACK_GAMES);
  const router = useRouter();
  const game = games[idx] || games[0];

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(async () => {
      const result = await NeuroAPI.getGames();
      if (cancelled || !result.ok || result.games.length === 0) return;
      setGames(result.games);
      setIdx((current) => Math.min(current, result.games.length - 1));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const prev = () => setIdx((idx - 1 + games.length) % games.length);
  const next = () => setIdx((idx + 1) % games.length);
  const play = () => router.push(`/games/${game.id}`);

  const gameName = isBuiltin(game.id) ? t(`games.${game.id}.name`) : game.name;
  const gameSkill = isBuiltin(game.id) ? t(`games.${game.id}.skill`) : game.cognitive_domain;

  return (
    <div className={styles.screen}>
      <div className={styles.wrapper}>
        <h1 className={styles.pageTitle}>{t("games.title")}</h1>
        <p className={styles.pageSubtitle}>{t("games.subtitle")}</p>

        <div className={styles.carouselArea}>
          <div className={styles.arrows}>
            <button className={styles.arrow} onClick={prev}>{t("actions.previous")}</button>
            <span className={styles.counter}>{idx + 1} / {games.length}</span>
            <button className={styles.arrow} onClick={next}>{t("actions.next")}</button>
          </div>

          <div className={styles.card} key={game.id}>
            <span className={styles.cardIcon}>{game.icon}</span>
            <div className={styles.cardName}>{gameName}</div>
            <div className={styles.cardSkill}>{gameSkill}</div>
            {!isBuiltin(game.id) && <div className={styles.moduleBadge}>Approved module</div>}
            <div className={styles.cardStars}>☆☆☆</div>
            <div className={styles.cardBest}>{t("games.best")}</div>
          </div>

          <div className={styles.dots}>
            {games.map((item, i) => (
              <div
                key={item.id}
                className={`${styles.dot} ${i === idx ? styles.dotOn : ""}`}
                onClick={() => setIdx(i)}
              />
            ))}
          </div>
        </div>

        <button className="btn btn-primary btn-large" onClick={play}>
          {t("actions.play", { game: gameName, icon: game.icon })}
        </button>
      </div>
    </div>
  );
}
