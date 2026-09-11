import Link from "next/link";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./GameCard.module.css";

interface GameCardProps {
  id: string;
  name: string;
  icon: string;
  cognitive_domain: string;
  description: string;
  difficulty_system: string;
  color: string;
}

export default function GameCard({
  id,
  name,
  icon,
  cognitive_domain,
  description,
  difficulty_system,
  color,
}: GameCardProps) {
  const { t } = useI18n();

  return (
    <div
      className={styles.card}
      style={{ "--card-accent": color } as React.CSSProperties}
    >
      {/* Glow accent */}
      <div className={styles.glowBar} />

      {/* Header */}
      <div className={styles.header}>
        <span className={styles.icon}>{icon}</span>
        <div>
          <h3 className={styles.name}>{name}</h3>
          <span className={styles.domain}>{cognitive_domain}</span>
        </div>
      </div>

      {/* Description */}
      <p className={styles.description}>{description}</p>

      {/* Footer */}
      <div className={styles.footer}>
        <span className={styles.badge}>{difficulty_system}</span>
        <Link href={`/games/${id}`} className={styles.playBtn}>
          {t("actions.play", { game: name, icon: "" })}
        </Link>
      </div>
    </div>
  );
}
