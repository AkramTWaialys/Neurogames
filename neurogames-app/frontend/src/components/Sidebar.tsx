"use client";

import { useState, useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { BookOpen, History, UploadCloud } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";
import { translatePerformanceLevel } from "@/i18n/labels";
import { logout as doLogout } from "@/lib/authApi";
import styles from "./Sidebar.module.css";

const GAMES = [
  { id: "gonogo",   icon: "🎯" },
  { id: "memory",   icon: "🃏" },
  { id: "tracking", icon: "👁️" },
  { id: "shapes",   icon: "🔷" },
  { id: "puzzle",   icon: "🧩" },
];

const NAV_ITEMS = [
  { href: "/games",   icon: "🕹️", labelKey: "nav.games" },
  { href: "/history", icon: "📋", labelKey: "nav.history" },
];

const DEVELOPER_NAV_ITEMS = [
  {
    tab: "guide",
    icon: BookOpen,
    label: "How to send a game",
  },
  {
    tab: "submit",
    icon: UploadCloud,
    label: "Games submission",
  },
  {
    tab: "history",
    icon: History,
    label: "History",
  },
];

export default function Sidebar() {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [gamesOpen, setGamesOpen] = useState(false);
  const [player, setPlayer] = useState<{ id?: string; cognitiveLevel?: string } | null>(null);
  const isDeveloperRoute = pathname.startsWith("/developer");
  const activeTab = searchParams.get("tab") || "guide";

  useEffect(() => {
    queueMicrotask(() => {
      try {
        const p = JSON.parse(localStorage.getItem("neuro_player") || "null");
        setPlayer(p);
      } catch { /* ignore */ }
    });
  }, [pathname]);

  // Hide on login/register, active game sessions, and dashboard pages
  if (
    pathname === "/" ||
    pathname === "/register" ||
    pathname.match(/^\/games\/[a-z]/) ||
    pathname.startsWith("/dashboard") ||
    pathname.startsWith("/admin")
  ) return null;

  const handleLogout = () => {
    doLogout();
    router.push("/");
  };

  if (isDeveloperRoute) {
    return (
      <aside className={styles.sidebar}>
        <Link href="/developer/game-requests?tab=guide" className={styles.brand}>
          <span className={styles.brandIcon}>🧠</span>
          <span className={styles.brandName}>NeuroGames</span>
        </Link>

        <nav className={styles.nav}>
          {DEVELOPER_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.tab;

            return (
              <Link
                key={item.tab}
                href={`/developer/game-requests?tab=${item.tab}`}
                className={`${styles.navItem} ${isActive ? styles.navActive : ""}`}
              >
                <span className={styles.navIcon}><Icon size={22} /></span>
                <span className={styles.navLabel}>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className={styles.spacer} />

        <button className={styles.logoutBtn} onClick={handleLogout}>
          🚪 {t("actions.logout")}
        </button>
      </aside>
    );
  }

  return (
    <aside className={styles.sidebar}>
      {/* ── Brand ──────────────────────────────────── */}
      <Link href="/games" className={styles.brand}>
        <span className={styles.brandIcon}>🧠</span>
        <span className={styles.brandName}>NeuroGames</span>
      </Link>

      {/* ── Navigation ─────────────────────────────── */}
      <nav className={styles.nav}>
        {NAV_ITEMS.map(item => {
          const isGames = item.href === "/games";
          const isActive = pathname === item.href || (isGames && pathname.startsWith("/games"));

          return (
            <div key={item.href}>
              {isGames ? (
                <>
                  <button
                    className={`${styles.navItem} ${isActive ? styles.navActive : ""}`}
                    onClick={() => setGamesOpen(!gamesOpen)}
                  >
                    <span className={styles.navIcon}>{item.icon}</span>
                    <span className={styles.navLabel}>{t(item.labelKey)}</span>
                    <span className={`${styles.chevron} ${gamesOpen ? styles.chevronOpen : ""}`}>›</span>
                  </button>

                  {/* Game sub-items */}
                  <div className={`${styles.subMenu} ${gamesOpen ? styles.subMenuOpen : ""}`}>
                    {GAMES.map(game => (
                      <Link
                        key={game.id}
                        href={`/games/${game.id}`}
                        className={`${styles.subItem} ${pathname === `/games/${game.id}` ? styles.subActive : ""}`}
                      >
                        <span className={styles.subIcon}>{game.icon}</span>
                        <span>{t(`games.${game.id}.name`)}</span>
                      </Link>
                    ))}
                  </div>
                </>
              ) : (
                <Link
                  href={item.href}
                  className={`${styles.navItem} ${isActive ? styles.navActive : ""}`}
                >
                  <span className={styles.navIcon}>{item.icon}</span>
                  <span className={styles.navLabel}>{t(item.labelKey)}</span>
                </Link>
              )}
            </div>
          );
        })}
      </nav>

      {/* ── Spacer ─────────────────────────────────── */}
      <div className={styles.spacer} />

      {/* ── Profile (bottom) ───────────────────────── */}
      <Link
        href="/profile"
        className={`${styles.profileCard} ${pathname === "/profile" ? styles.profileActive : ""}`}
      >
        <div className={styles.avatar}>🦁</div>
        <div className={styles.profileInfo}>
          <div className={styles.profileName}>{player?.id || t("nav.profile")}</div>
          <div className={styles.profileLevel}>
            {translatePerformanceLevel(player?.cognitiveLevel || "Medium", t)}
          </div>
        </div>
      </Link>

      {/* ── Logout Button ──────────────────────────── */}
      <button className={styles.logoutBtn} onClick={handleLogout}>
        🚪 {t("actions.logout")}
      </button>
    </aside>
  );
}
