"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { register, registerDeveloper, getSchools, School } from "@/lib/authApi";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./page.module.css";
import Link from "next/link";
import ConnersAssessment from "@/components/ConnersAssessment";

const AVATARS = [
  { emoji: "🦁", key: "avatar.lion",      bg: "#FEF3C7", border: "#F59E0B" },
  { emoji: "🐼", key: "avatar.panda",     bg: "#DBEAFE", border: "#60A5FA" },
  { emoji: "🐸", key: "avatar.frog",      bg: "#D1FAE5", border: "#34D399" },
  { emoji: "🦊", key: "avatar.fox",       bg: "#FED7AA", border: "#FB923C" },
  { emoji: "🐧", key: "avatar.penguin",   bg: "#EDE9FE", border: "#A78BFA" },
  { emoji: "🦋", key: "avatar.butterfly", bg: "#FCE7F3", border: "#F472B6" },
  { emoji: "🐬", key: "avatar.dolphin",   bg: "#CFFAFE", border: "#22D3EE" },
  { emoji: "🦄", key: "avatar.unicorn",   bg: "#FDF4FF", border: "#C084FC" },
];

function ageToGroup(age: number) {
  if (age <= 8) return "6-8";
  if (age <= 11) return "9-11";
  return "12-14";
}

export default function RegisterPage() {
  const { t } = useI18n();
  const router = useRouter();

  const [accountType, setAccountType] = useState<"child" | "developer">("child");
  const [selectedAvatar, setSelectedAvatar] = useState(0);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [age, setAge] = useState(9);
  const [cluster] = useState("Optimal / Neurotypical");
  const [schools, setSchools] = useState<School[]>([]);
  const [schoolId, setSchoolId] = useState<number | "">("");
  const [customSchoolName, setCustomSchoolName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  // Conners Assessment state
  const [showConners, setShowConners] = useState(false);
  const [connersScore, setConnersScore] = useState<number | undefined>(undefined);
  const [connersData, setConnersData] = useState<Record<number, number>>({});

  useEffect(() => {
    getSchools().then(data => {
      setSchools(data);
      if (data.length > 0) {
        setSchoolId((current) => current === "" ? data[0].id : current);
      }
    });
  }, []);

  const handleRegister = async () => {
    if (!username.trim() || !password) return;
    if (username.trim().length < 2) {
      setError("Username must be at least 2 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError(t("auth.passwordMismatch"));
      return;
    }
    if (password.length < 4) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    if (accountType === "developer" && !email.trim()) {
      setError("Developer email is required.");
      return;
    }
    if (accountType === "child" && schoolId === "") {
      setError("Please select a school.");
      return;
    }

    setLoading(true);
    setError("");

    const result =
      accountType === "developer"
        ? await registerDeveloper({
            username: username.trim(),
            password,
            email: email.trim(),
            phone: phone.trim() || undefined,
          })
        : await register({
            username: username.trim(),
            password,
            email: email.trim() || undefined,
            phone: phone.trim() || undefined,
            role: "child",
            display_name: displayName.trim() || undefined,
            avatar: AVATARS[selectedAvatar].emoji,
            school_id: schoolId === -1 ? undefined : Number(schoolId),
            custom_school_name: schoolId === -1 ? customSchoolName.trim() : undefined,
            age,
            age_group: ageToGroup(age),
            cognitive_level: "Medium",
            cluster,
            conners_score: showConners ? connersScore : undefined,
            conners_data: showConners ? JSON.stringify(connersData) : undefined,
          });

    if (result.ok) {
      setSuccess(true);
      setTimeout(() => router.push("/"), 2000);
    } else {
      setError(result.error || t("auth.registerError"));
    }
    setLoading(false);
  };

  const canSubmit =
    Boolean(username.trim() && password) &&
    (accountType === "child" || Boolean(email.trim()));

  if (success) {
    return (
      <div className={styles.screen}>
        <div className={styles.wrapper}>
          <div className={styles.brand}>✅</div>
          <h1 className={styles.title}>{t("auth.registerSuccess")}</h1>
          <p className={styles.subtitle}>{t("auth.redirecting")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      <div className={styles.wrapper}>
        {/* Brand */}
        <div className={styles.brand}>🧠</div>
        <h1 className={styles.title}>{t("auth.createAccount")}</h1>
        <p className={styles.subtitle}>
          {accountType === "child"
            ? "Parent creates the child's account."
            : "Developer creates an account to request a compatible ADHD game module."}
        </p>

        <div className={styles.switchCard}>
          <button
            type="button"
            className={`${styles.switchBtn} ${accountType === "child" ? styles.switchActive : ""}`}
            onClick={() => setAccountType("child")}
          >
            Child account
          </button>
          <button
            type="button"
            className={`${styles.switchBtn} ${accountType === "developer" ? styles.switchActive : ""}`}
            onClick={() => setAccountType("developer")}
          >
            Developer account
          </button>
        </div>

        {/* Avatar Picker */}
        {accountType === "child" && (
          <div className={styles.card}>
            <span className={styles.label}>{t("login.chooseAvatar")}</span>
            <div className={styles.avatarGrid}>
              {AVATARS.map((av, i) => (
                <button
                  key={i}
                  className={`${styles.avatarItem} ${i === selectedAvatar ? styles.avatarSel : ""}`}
                  style={{ background: av.bg, borderColor: av.border }}
                  onClick={() => setSelectedAvatar(i)}
                >
                  {av.emoji}
                  <span className={styles.avatarName}>{t(av.key)}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Username */}
        <div className={styles.card}>
          <span className={styles.label}>{t("auth.username")}</span>
          <input
            id="register-username"
            className={styles.input}
            type="text"
            placeholder={t("auth.usernamePlaceholder")}
            maxLength={30}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </div>

        {/* Email */}
        <div className={styles.card}>
          <span className={styles.label}>Email {accountType === "developer" ? "(required)" : "(parent/contact)"}</span>
          <input
            id="register-email"
            className={styles.input}
            type="email"
            placeholder="name@example.com"
            maxLength={120}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
        </div>

        {/* Phone */}
        <div className={styles.card}>
          <span className={styles.label}>Phone number</span>
          <input
            id="register-phone"
            className={styles.input}
            type="tel"
            placeholder="+216 ..."
            maxLength={40}
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            autoComplete="tel"
          />
        </div>

        {/* Display Name */}
        {accountType === "child" && <div className={styles.card}>
          <span className={styles.label}>{t("auth.nickname")}</span>
          <input
            id="register-displayname"
            className={styles.input}
            type="text"
            placeholder={t("auth.nicknamePlaceholder")}
            maxLength={20}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>}

        {/* School */}
        {accountType === "child" && <div className={styles.card}>
          <span className={styles.label}>School / Organization</span>
          <select
            id="register-school"
            className={styles.input}
            value={schoolId}
            onChange={(e) => setSchoolId(Number(e.target.value))}
            disabled={schools.length === 0}
          >
            {schools.length === 0 && <option value="">Loading schools...</option>}
            {schools.filter(s => s.id !== 1).map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
            <option value="-1">Autre / Saisir mon école</option>
          </select>
        </div>}

        {accountType === "child" && schoolId === -1 && (
          <div className={styles.card}>
            <span className={styles.label}>Nom de votre école</span>
            <input
              className={styles.input}
              type="text"
              placeholder="e.g. Ecole Internationale"
              value={customSchoolName}
              onChange={(e) => setCustomSchoolName(e.target.value)}
              required
            />
          </div>
        )}

        {/* Age */}
        {accountType === "child" && <div className={styles.card}>
          <span className={styles.label}>{t("auth.age")}</span>
          <select
            id="register-age"
            className={styles.input}
            value={age}
            onChange={(e) => setAge(Number(e.target.value))}
          >
            {Array.from({ length: 11 }, (_, i) => i + 6).map((a) => (
              <option key={a} value={a}>{a} {t("auth.years")}</option>
            ))}
          </select>
        </div>}

        {/* Conners Assessment Toggle */}
        {accountType === "child" && <div className={styles.card}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span className={styles.label} style={{ marginBottom: 0 }}>
              Dépistage Conners (conseillé)
            </span>
            <input
              type="checkbox"
              style={{ width: "22px", height: "22px", cursor: "pointer" }}
              checked={showConners}
              onChange={(e) => setShowConners(e.target.checked)}
            />
          </div>
          {showConners && (
            <div style={{ marginTop: "16px", borderTop: "1px solid #E5E7EB", paddingTop: "16px" }}>
              <ConnersAssessment 
                onScoreChange={(score, data) => {
                  setConnersScore(score);
                  setConnersData(data);
                }} 
              />
            </div>
          )}
        </div>}

        {/* ADHD Diagnosis */}

        {/* Password */}
        <div className={`${styles.card} ${styles.cardTeal}`}>
          <span className={styles.labelTeal}>{t("auth.password")}</span>
          <input
            id="register-password"
            className={styles.input}
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
        </div>

        {/* Confirm Password */}
        <div className={`${styles.card} ${styles.cardTeal}`}>
          <span className={styles.labelTeal}>{t("auth.confirmPassword")}</span>
          <input
            id="register-confirm-password"
            className={styles.input}
            type="password"
            placeholder="••••••••"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRegister()}
            autoComplete="new-password"
          />
        </div>

        {/* Error */}
        {error && <p className={styles.error}>{error}</p>}

        {/* Submit */}
        <button
          id="register-submit"
          className="btn btn-primary btn-large"
          onClick={handleRegister}
          disabled={!canSubmit || loading}
          style={{ opacity: canSubmit ? 1 : 0.5 }}
        >
          {loading ? t("common.loading") : t("auth.register")}
        </button>

        {/* Back to login link */}
        <p className={styles.registerLink}>
          {t("auth.hasAccount")}{" "}
          <Link href="/">{t("actions.login")}</Link>
        </p>
      </div>
    </div>
  );
}
