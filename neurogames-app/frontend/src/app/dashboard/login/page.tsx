"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { adminLogin } from "@/lib/adminAuth";
import { useI18n } from "@/i18n/I18nProvider";
import "../admin.css";

export default function AdminLoginPage() {
  const { t } = useI18n();
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  useEffect(() => {
    router.replace("/");
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;

    setLoading(true);
    setError("");

    const result = await adminLogin(password);
    if (result.authenticated) {
      router.push("/dashboard/overview");
    } else {
      setError(result.error || t("admin.loginError"));
    }
    setLoading(false);
  };

  return (
    <div className="loginScreen">
      <form className="loginCard" onSubmit={handleSubmit}>
        <div className="loginBrandIcon">🧠</div>
        <h1>{t("common.appName")}</h1>
        <div className="loginSubtitle">{t("admin.administration")}</div>

        <input
          className="loginInput"
          type="password"
          placeholder={t("admin.password")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />

        <button className="loginBtn" type="submit" disabled={loading || !password.trim()}>
          {loading ? t("actions.loggingIn") : t("actions.login")}
        </button>

        {error && <div className="loginError">{error}</div>}
      </form>
    </div>
  );
}
