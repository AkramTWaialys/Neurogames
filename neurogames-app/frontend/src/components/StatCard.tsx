import styles from "./StatCard.module.css";

interface StatCardProps {
  icon: string;
  value: string | number;
  label: string;
  accent?: string;
}

export default function StatCard({ icon, value, label, accent }: StatCardProps) {
  return (
    <div
      className={styles.card}
      style={accent ? ({ "--stat-accent": accent } as React.CSSProperties) : undefined}
    >
      <span className={styles.icon}>{icon}</span>
      <span className={styles.value}>{value}</span>
      <span className={styles.label}>{label}</span>
    </div>
  );
}
