"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./Navbar.module.css";

const NAV_ITEMS = [
  { href: "/", labelKey: "common.appName", icon: "🏠" },
  { href: "/games", labelKey: "nav.games", icon: "🎮" },
  { href: "/profile", labelKey: "nav.profile", icon: "👤" },
];

export default function Navbar() {
  const { t } = useI18n();
  const pathname = usePathname();

  return (
    <nav className={styles.navbar}>
      <div className={styles.inner}>
        {/* Brand */}
        <Link href="/" className={styles.brand}>
          <span className={styles.brandIcon}>🧠</span>
          <span className={styles.brandText}>NeuroGames</span>
        </Link>

        {/* Nav links */}
        <ul className={styles.links}>
          {NAV_ITEMS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`${styles.link} ${
                  pathname === item.href ? styles.active : ""
                }`}
              >
                <span className={styles.linkIcon}>{item.icon}</span>
                <span className={styles.linkLabel}>{t(item.labelKey)}</span>
              </Link>
            </li>
          ))}
        </ul>

        {/* Status dot */}
        <div className={styles.statusDot} title={t("common.synced")}>
          <span className={styles.dot} />
          <span className={styles.statusLabel}>{t("common.synced")}</span>
        </div>
      </div>
    </nav>
  );
}
