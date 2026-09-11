"use client";

import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useI18n } from "@/i18n/I18nProvider";
import { translatePerformanceLevel } from "@/i18n/labels";
import styles from "./PlayerHeader.module.css";

export default function PlayerHeader() {
  const { t } = useI18n();
  const pathname = usePathname();
  const [player, setPlayer] = useState<{ id?: string; cognitiveLevel?: string } | null>(null);

  useEffect(() => {
    queueMicrotask(() => {
      try {
        const p = JSON.parse(localStorage.getItem("neuro_player") || "null");
        setPlayer(p);
      } catch { /* ignore */ }
    });
  }, [pathname]);

  // Hide on login page and game sessions
  if (pathname === "/" || pathname.startsWith("/games/")) return null;

  return (
    <header className={styles.header}>
      <Link href="/games" className={styles.homeBtn}>
        <span className={styles.homeIcon}>🏠</span>
        <span className={styles.homeLabel}>{t("nav.games")}</span>
      </Link>

      <Link href="/history" className={styles.homeBtn}>
        <span className={styles.homeIcon}>📋</span>
        <span className={styles.homeLabel}>{t("nav.history")}</span>
      </Link>

      <Link href="/profile" className={styles.profileLink}>
        <div className={styles.avatar}>🦁</div>
        <div className={styles.info}>
          <div className={styles.name}>{player?.id || t("nav.profile")}</div>
          <div className={styles.level}>
            {translatePerformanceLevel(player?.cognitiveLevel || "Medium", t)}
          </div>
        </div>
      </Link>
    </header>
  );
}
