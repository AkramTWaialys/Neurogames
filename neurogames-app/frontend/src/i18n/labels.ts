import type { TranslationParams } from "./messages";

type TFunction = (key: string, params?: TranslationParams) => string;

const METRIC_LABEL_KEYS: Record<string, string> = {
  "Correct / Total": "metrics.correctTotal",
  "Précision": "metrics.accuracy",
  "📊 Précision": "metrics.accuracy",
  "🎯 Précision global": "metrics.accuracy",
  "Temps réaction": "metrics.reactionTime",
  "⏱️ RT moyen": "metrics.avgReactionTime",
  "⏱ RT moyen": "metrics.avgReactionTime",
  "Rounds joués": "metrics.roundsPlayed",
  "Performance": "metrics.performance",
  "Correct": "metrics.correctTotal",
  "Réaction": "metrics.reaction",
  "Niveau": "metrics.level",
  "Durée": "metrics.duration",
  "🃏 Paires trouvées": "metrics.pairsFound",
  "🔄 Essais": "metrics.attempts",
  "🔄 Essais totaux": "metrics.totalAttempts",
  "💡 Conseil": "metrics.advice",
  "🎯 Cibles touchées": "metrics.targetsHit",
  "❌ Ratées": "metrics.missed",
  "❌ Ratés": "metrics.missed",
  "🎯 Hits": "metrics.hits",
  "✅ Hits": "metrics.hits",
  "❌ Fausses alarmes": "metrics.falseAlarms",
  "😶 Ratés": "metrics.missed",
  "🛡️ Bonnes rejections": "metrics.correctRejections",
  "⚡ Variabilité": "metrics.variability",
  "🎯 Niveau final": "metrics.finalLevel",
  "✅ Réussites": "metrics.successes",
  "❌ Erreurs": "metrics.errors",
  "🧩 Pièces correctes": "metrics.piecesCorrect",
  "🏆 Score total": "metrics.totalScore",
  "📊 Niveau atteint": "metrics.levelReached",
  "⏱ Durée session": "metrics.sessionDuration",
  "⏱️ Temps": "metrics.time",
  "🏆 Niveaux réussis": "metrics.levelsWon",
  "🔀 Coups totaux": "metrics.totalMoves",
};

const PERFORMANCE_KEYS: Record<string, string> = {
  Optimal: "performance.Optimal",
  Struggling: "performance.Struggling",
  Disengaged: "performance.Disengaged",
  Advanced: "performance.Advanced",
  Early: "performance.Early",
  Developing: "performance.Developing",
  High: "level.advanced",
  Low: "level.beginner",
  Medium: "level.intermediate",
  Expert: "level.expert",
  Avancé: "level.advanced",
  Débutant: "level.beginner",
  Intermédiaire: "level.intermediate",
};

const CLINICAL_PROFILE_KEYS: Record<string, string> = {
  "Optimal / Neurotypical": "profile.optimal",
  "Inattentive ADHD": "profile.inattentive",
  "Hyperactive-Impulsive ADHD": "profile.hyperactive",
  "Combined ADHD": "profile.combined",
  "Moderate Difficulty": "profile.moderateDifficulty",
  "At-Risk / Clinical": "profile.atRiskClinical",
  "Severe / Disengaged": "profile.severeDisengaged",
  Unknown: "common.unknown",
};

const CONNERS_TIER_KEYS: Record<string, string> = {
  typical: "conners.tier.typical",
  borderline: "conners.tier.borderline",
  elevated: "conners.tier.elevated",
  significant: "conners.tier.significant",
};

const GAME_KEY_BY_NAME: Record<string, string> = {
  "Go / No-Go": "gonogo",
  "Memory Match": "memory",
  "Visual Tracking": "tracking",
  "Shape Builder": "shapes",
  "Puzzle Quest": "puzzle",
};

export function translateMetricLabel(label: string, t: TFunction): string {
  const key = METRIC_LABEL_KEYS[label];
  return key ? t(key) : label;
}

export function translatePerformanceLevel(level: string | null | undefined, t: TFunction): string {
  if (!level) return "";
  const key = PERFORMANCE_KEYS[level];
  return key ? t(key) : level;
}

export function translateClinicalProfile(profile: string | null | undefined, t: TFunction): string {
  if (!profile) return "";
  const key = CLINICAL_PROFILE_KEYS[profile];
  return key ? t(key) : profile;
}

export function translateConnersTier(tier: string | null | undefined, t: TFunction): string {
  if (!tier) return "";
  const key = CONNERS_TIER_KEYS[tier.toLowerCase()];
  return key ? t(key) : tier;
}

export function translateGameName(game: string | null | undefined, t: TFunction): string {
  if (!game) return "";
  const gameId = GAME_KEY_BY_NAME[game] || game;
  const key = `games.${gameId}.name`;
  const translated = t(key);
  return translated === key ? game : translated;
}

export function translateGameSkill(game: string, t: TFunction): string {
  return t(`games.${game}.skill`);
}
