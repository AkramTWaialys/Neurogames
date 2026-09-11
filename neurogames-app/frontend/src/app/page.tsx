"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { login, isLoggedIn, getMe } from "@/lib/authApi";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./page.module.css";
import Link from "next/link";

function isDashboardRole(role?: string | null): boolean {
  return role === "therapist" || role === "admin" || role === "super_admin";
}

function isDeveloperRole(role?: string | null): boolean {
  return role === "developer";
}

export default function LoginPage() {
  const { t } = useI18n();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);
  const router = useRouter();

  // Auto-redirect if already logged in
  useEffect(() => {
    if (isLoggedIn()) {
      getMe().then((profile) => {
        if (profile) {
          if (isDashboardRole(profile.role)) {
            // Update neuro_admin for consistency with adminAuth helpers
            localStorage.setItem("neuro_admin", JSON.stringify({ role: profile.role, ts: Date.now() }));
            router.replace("/dashboard/overview");
          } else if (isDeveloperRole(profile.role)) {
            router.replace("/developer/game-requests");
          } else {
            router.replace("/games");
          }
        } else {
          setChecking(false);
        }
      });
    } else {
      queueMicrotask(() => setChecking(false));
    }
  }, [router]);

  const handleLogin = async () => {
    const name = username.trim();
    if (!name || !password) return;
    setLoading(true);
    setError("");

    const result = await login(name, password, rememberMe);
    if (result.ok && result.profile) {
      if (isDashboardRole(result.profile.role)) {
        localStorage.setItem("neuro_admin", JSON.stringify({ role: result.profile.role, ts: Date.now() }));
        router.push("/dashboard/overview");
      } else if (isDeveloperRole(result.profile.role)) {
        router.push("/developer/game-requests");
      } else {
        router.push("/games");
      }
    } else {
      setError(result.error || t("auth.loginError"));
    }
    setLoading(false);
  };

  if (checking) {
    return (
      <div className={styles.screen}>
        <div className={styles.wrapper}>
          <div className={styles.brand}>🧠</div>
          <p style={{ textAlign: "center", opacity: 0.6 }}>{t("common.loading")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      <div className={styles.wrapper}>
        {/* Brand */}
        <div className={styles.brand}>🧠</div>
        <h1 className={styles.title}>{t("common.appName")}</h1>
        <p className={styles.subtitle}>{t("login.subtitle")}</p>

        {/* Login Form */}
        <div className={styles.card}>
          <span className={styles.label}>{t("auth.username")}</span>
          <input
            id="login-username"
            className={styles.input}
            type="text"
            placeholder={t("auth.usernamePlaceholder")}
            maxLength={30}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            autoComplete="username"
          />
        </div>

        <div className={`${styles.card} ${styles.cardTeal}`}>
          <span className={styles.labelTeal}>{t("auth.password")}</span>
          <input
            id="login-password"
            className={styles.input}
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            autoComplete="current-password"
          />
        </div>

        {/* Stay Logged In */}
        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={rememberMe}
            onChange={(e) => setRememberMe(e.target.checked)}
          />
          <span>{t("auth.stayLoggedIn")}</span>
        </label>

        {/* Error */}
        {error && <p className={styles.error}>{error}</p>}

        {/* Login Button */}
        <button
          id="login-submit"
          className="btn btn-primary btn-large"
          onClick={handleLogin}
          disabled={!username.trim() || !password || loading}
          style={{ opacity: username.trim() && password ? 1 : 0.5 }}
        >
          {loading ? t("actions.loggingIn") : t("actions.login")}
        </button>

        {/* Register Link */}
        <p className={styles.registerLink}>
          {t("auth.parentPrompt")}{" "}
          <Link href="/register">{t("auth.createAccount")}</Link>
        </p>
      </div>
    </div>
  );
}
