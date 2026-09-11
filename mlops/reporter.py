"""
MLOps Cognitive Performance Report Generator — v2
===================================================
Generates structured cognitive performance summaries for participants
by translating raw ML pipeline outputs into readable prose.

Key improvements over v1:
  - Structured JSON output schema (LLM fills structure, we render markdown)
  - Output validation & hallucination guard
  - Enriched context with all available game-specific features
  - Rich template fallback with clinical interpretations
  - Structured logging, retry logic, and report caching

Usage:
    from mlops.reporter import generate_report

    result = generate_report("P001", pipeline_summary)
    print(result["report_text"])

CLI:
    python mlops/reporter.py --participant P001
    python mlops/reporter.py --participant P001 --compare
    python mlops/reporter.py --all --compare
"""

import os
import re
import sys
import json
import time
import hashlib
import logging
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_local_env() -> None:
    """Load local dotenv-style secrets without overriding real environment vars."""
    for env_path in (os.path.join(_ROOT, ".env"), os.path.join(_ROOT, ".env.local")):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            pass


_load_local_env()

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

import pandas as pd

from mlops.config import (
    GAME_NAMES, CLUSTER_LABELS, REPORTS_DIR,
    MLOPS_OUTPUTS_DIR, PROJECT_ROOT,
    GAME_DISPLAY_NAMES, GAME_DOMAINS,
)
from mlops.db import load_game_df, list_participant_ids

# ── Logging ────────────────────────────────────────────────────────────
log = logging.getLogger("mlops.reporter")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s — %(message)s", datefmt="%H:%M:%S"
    ))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)


# ── LLM provider configuration ───────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "300"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "8192"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODELS = [
    name.strip()
    for name in os.environ.get(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-2.5-flash",
    ).split(",")
    if name.strip()
]
GEMINI_API_BASE = os.environ.get(
    "GEMINI_API_BASE",
    "https://generativelanguage.googleapis.com/v1beta",
)
GEMINI_THINKING_BUDGET = os.environ.get("GEMINI_THINKING_BUDGET", "0").strip()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:4b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", str(LLM_TIMEOUT)))
OLLAMA_MAX_RETRIES = int(os.environ.get("OLLAMA_MAX_RETRIES", str(LLM_MAX_RETRIES)))

SUPPORTED_REPORT_LOCALES = {"fr", "en", "ar"}
DEFAULT_REPORT_LOCALE = "en"
REPORT_I18N_VERSION = 3

REPORT_LOCALE_INSTRUCTIONS = {
    "en": "Write all human-readable report text in English.",
    "fr": "Write all human-readable report text in French.",
    "ar": (
        "Write all human-readable report text in Arabic. Use clear Modern Standard "
        "Arabic suitable for parents, teachers, and therapists."
    ),
}


def normalize_report_locale(locale: str | None) -> str:
    """Return a supported report locale code."""
    if not locale:
        return DEFAULT_REPORT_LOCALE
    short = str(locale).strip().lower().split("-")[0]
    return short if short in SUPPORTED_REPORT_LOCALES else DEFAULT_REPORT_LOCALE

# GAME_DOMAINS and GAME_DISPLAY_NAMES are imported from mlops.config
# (the single source of truth — also used to seed the games registry table)

GAME_DOMAINS_I18N = {
    "en": GAME_DOMAINS,
    "fr": {
        "gonogo": "Controle inhibiteur",
        "memory": "Memoire de travail",
        "tracking": "Attention visuelle",
        "shapes": "Raisonnement spatial",
        "puzzle": "Planification et flexibilite cognitive",
    },
    "ar": {
        "gonogo": "التحكم في الاندفاع",
        "memory": "الذاكرة العاملة",
        "tracking": "الانتباه البصري",
        "shapes": "الاستدلال المكاني",
        "puzzle": "التخطيط والمرونة المعرفية",
    },
}

GAME_DISPLAY_NAMES_I18N = {
    "en": GAME_DISPLAY_NAMES,
    "fr": {
        "gonogo": "Go / No-Go",
        "memory": "Memory Match",
        "tracking": "Visual Tracking",
        "shapes": "Tri des formes",
        "puzzle": "Puzzle",
    },
    "ar": {
        "gonogo": "Go / No-Go",
        "memory": "لعبة الذاكرة",
        "tracking": "التتبع البصري",
        "shapes": "ترتيب الأشكال",
        "puzzle": "الألغاز",
    },
}

REPORT_COPY = {
    "en": {
        "report_type.progress": "Progress Report",
        "report_type.assessment": "Assessment Summary",
        "unknown": "Unknown",
        "no_data": "No gameplay data is available yet for this child.",
        "overview_total": "The child has completed {sessions} gameplay sessions across {games} activities.",
        "overview_strengths": "Current strengths are most visible in {items}.",
        "overview_challenges": "The activities needing the most support are {items}.",
        "overview_developing": "Performance is generally developing across the available activities.",
        "no_detail_game": "The child has played {game} but there is not enough data yet for a detailed interpretation.",
        "not_enough_progress": "Not enough sessions yet to assess progress (only {sessions} sessions played). At least 5 sessions are needed.",
        "framing.large": "Over the last 10 sessions compared to the child's initial baseline",
        "framing.medium": "Over the past few sessions compared to earlier",
        "framing.small": "Since the child started",
        "accuracy.excellent": "The child answered almost all questions correctly, showing excellent mastery of this activity.",
        "accuracy.good": "The child performed well, answering most questions correctly with good consistency.",
        "accuracy.moderate": "The child answered correctly about 7 out of 10 times, which is within the expected range but shows room for growth.",
        "accuracy.low": "The child had some difficulty with this activity, answering correctly about half the time. This may benefit from additional practice.",
        "accuracy.very_low": "The child found this activity quite challenging, with many incorrect answers. This area may need closer attention and support.",
        "rt.very_fast": "The child responded very quickly, showing fast processing and good attentiveness.",
        "rt.normal": "Response times were within the normal range, showing appropriate processing speed.",
        "rt.slow": "The child took a bit longer to respond than expected, which may indicate they need more time to process information.",
        "rt.delayed": "Response times were noticeably slow. The child may be struggling to focus or needs more time to think through their answers.",
        "rt.very_delayed": "The child's response times were very slow, which could suggest difficulty sustaining attention during the activity.",
        "frustration.low": "The child showed very few signs of frustration during gameplay, indicating a comfortable experience.",
        "frustration.medium": "Some signs of frustration were observed. The child may have felt challenged or impatient at times during the activity.",
        "frustration.high": "The child showed frequent signs of frustration, which may indicate the activity felt too difficult or overwhelming.",
        "severity.normal": "The child's performance in this area is within the expected range, with no concerns at this time.",
        "severity.mild": "There are minor signs that deserve monitoring. Nothing alarming, but worth keeping an eye on over the coming weeks.",
        "severity.moderate": "The child shows some difficulties in this area that may benefit from targeted support or additional practice.",
        "severity.severe": "There are significant challenges in this area. A more detailed assessment by a specialist may be helpful.",
        "cluster.combined": "The child's play patterns suggest both attention regulation and impulse management are areas for growth, which often responds well to structured support.",
        "cluster.hyperactive": "The child's play patterns suggest they may benefit from activities that build impulse management and waiting skills, while their attention skills appear relatively strong.",
        "cluster.inattentive": "The child's play patterns suggest they may benefit from activities that strengthen sustained focus, while their impulse management appears relatively strong.",
        "cluster.optimal": "The child's play patterns are within the typical range, with no significant attention or impulse management concerns identified.",
        "improve.correct.big_up": "Accuracy has improved significantly. The child is getting noticeably more answers correct compared to earlier sessions.",
        "improve.correct.up": "Accuracy has improved. The child is getting more answers correct than before.",
        "improve.correct.big_down": "Accuracy has decreased compared to earlier sessions, which may indicate fatigue, disengagement, or increased difficulty.",
        "improve.correct.down": "Accuracy has dipped slightly compared to earlier sessions.",
        "improve.correct.stable": "Accuracy has remained stable over time.",
        "improve.rt.big_up": "Response times have gotten noticeably faster, suggesting improved processing speed and focus.",
        "improve.rt.up": "Response times have improved slightly, with the child reacting a bit faster than before.",
        "improve.rt.big_down": "Response times have slowed down compared to earlier sessions, which could indicate reduced focus or increased difficulty.",
        "improve.rt.down": "Response times have increased slightly.",
        "improve.rt.stable": "Response times have remained consistent over time.",
        "improve.frustration.big_up": "Frustration events have decreased significantly. The child appears much calmer and more comfortable during gameplay.",
        "improve.frustration.up": "Frustration events have decreased, suggesting the child is adapting well to the activities.",
        "improve.frustration.big_down": "Frustration events have increased, which may indicate the child is finding the activities more challenging.",
        "improve.frustration.down": "Frustration events have increased slightly.",
        "improve.frustration.stable": "Frustration levels have remained about the same.",
        "rec.specialist": "Consider scheduling a meeting with a specialist to discuss the areas where the child is struggling. This does not mean anything is wrong; it just helps get a clearer picture.",
        "rec.breaks": "The child showed signs of frustration during some activities. Try shorter play sessions with breaks in between to keep the experience positive.",
        "rec.focus": "The child may benefit from focus-building activities at home, like puzzles, building blocks, or simple card games that require paying attention.",
        "rec.regress": "Some scores have dipped recently. This can happen because of tiredness, a difficult day, or needing a break. Keep an eye on it over the next few weeks.",
        "rec.progress": "The child is showing positive progress. Continue with the current activities and keep the sessions regular.",
        "rec.check_back": "Check back in about a month to see how things are progressing.",
    },
    "fr": {
        "report_type.progress": "Rapport de progression",
        "report_type.assessment": "Bilan d'evaluation",
        "unknown": "Inconnu",
        "no_data": "Aucune donnee de jeu n'est encore disponible pour cet enfant.",
        "overview_total": "L'enfant a complete {sessions} sessions de jeu a travers {games} activites.",
        "overview_strengths": "Les points forts actuels sont les plus visibles dans {items}.",
        "overview_challenges": "Les activites necessitant le plus de soutien sont {items}.",
        "overview_developing": "Les performances se developpent globalement a travers les activites disponibles.",
        "no_detail_game": "L'enfant a joue a {game}, mais il n'y a pas encore assez de donnees pour une interpretation detaillee.",
        "not_enough_progress": "Pas assez de sessions pour evaluer la progression (seulement {sessions} sessions jouees). Il faut au moins 5 sessions.",
        "framing.large": "Au cours des 10 dernieres sessions par rapport au niveau initial de l'enfant",
        "framing.medium": "Au cours des dernieres sessions par rapport aux precedentes",
        "framing.small": "Depuis que l'enfant a commence",
        "accuracy.excellent": "L'enfant a repondu correctement a presque toutes les questions, montrant une excellente maitrise de cette activite.",
        "accuracy.good": "L'enfant a bien performe, repondant correctement a la plupart des questions avec une bonne regularite.",
        "accuracy.moderate": "L'enfant a repondu correctement environ 7 fois sur 10, ce qui est dans la moyenne mais montre une marge de progression.",
        "accuracy.low": "L'enfant a rencontre quelques difficultes avec cette activite, repondant correctement environ la moitie du temps. Un entrainement supplementaire pourrait etre benefique.",
        "accuracy.very_low": "L'enfant a trouve cette activite assez difficile, avec beaucoup de reponses incorrectes. Ce domaine peut necessiter une attention et un soutien plus rapproches.",
        "rt.very_fast": "L'enfant a repondu tres rapidement, montrant une bonne vitesse de traitement et une bonne attention.",
        "rt.normal": "Les temps de reponse etaient dans la plage normale, montrant une vitesse de traitement appropriee.",
        "rt.slow": "L'enfant a mis un peu plus de temps que prevu pour repondre, ce qui peut indiquer qu'il a besoin de plus de temps pour traiter les informations.",
        "rt.delayed": "Les temps de reponse etaient notablement lents. L'enfant peut avoir du mal a se concentrer ou a besoin de plus de temps pour reflechir.",
        "rt.very_delayed": "Les temps de reponse de l'enfant etaient tres lents, ce qui pourrait suggerer une difficulte a maintenir l'attention pendant l'activite.",
        "frustration.low": "L'enfant a montre tres peu de signes de frustration pendant le jeu, indiquant une experience confortable.",
        "frustration.medium": "Quelques signes de frustration ont ete observes. L'enfant a peut-etre ressenti un defi ou de l'impatience par moments pendant l'activite.",
        "frustration.high": "L'enfant a montre des signes frequents de frustration, ce qui peut indiquer que l'activite etait trop difficile ou accablante.",
        "severity.normal": "Les performances de l'enfant dans ce domaine sont dans la plage attendue, sans preoccupation particuliere pour le moment.",
        "severity.mild": "Il y a des signes mineurs qui meritent d'etre surveilles. Rien d'alarmant, mais a garder a l'oeil au cours des prochaines semaines.",
        "severity.moderate": "L'enfant montre quelques difficultes dans ce domaine qui pourraient beneficier d'un soutien cible ou d'un entrainement supplementaire.",
        "severity.severe": "Il y a des defis importants dans ce domaine. Une evaluation plus detaillee par un specialiste pourrait etre utile.",
        "cluster.combined": "Les habitudes de jeu de l'enfant suggerent que la regulation de l'attention et la gestion des impulsions sont des domaines de croissance, ce qui repond generalement bien a un soutien structure.",
        "cluster.hyperactive": "Les habitudes de jeu de l'enfant suggerent qu'il pourrait beneficier d'activites qui developpent la gestion des impulsions et les competences d'attente, tandis que ses competences attentionnelles semblent relativement solides.",
        "cluster.inattentive": "Les habitudes de jeu de l'enfant suggerent qu'il pourrait beneficier d'activites qui renforcent la concentration soutenue, tandis que sa gestion des impulsions semble relativement solide.",
        "cluster.optimal": "Les habitudes de jeu de l'enfant sont dans la plage typique, sans preoccupations significatives en matiere d'attention ou de gestion des impulsions.",
        "improve.correct.big_up": "La precision s'est amelioree de facon significative. L'enfant obtient nettement plus de bonnes reponses par rapport aux sessions precedentes.",
        "improve.correct.up": "La precision s'est amelioree. L'enfant obtient plus de bonnes reponses qu'avant.",
        "improve.correct.big_down": "La precision a diminue par rapport aux sessions precedentes, ce qui peut indiquer de la fatigue, un desengagement ou une difficulte accrue.",
        "improve.correct.down": "La precision a legerement diminue par rapport aux sessions precedentes.",
        "improve.correct.stable": "La precision est restee stable au fil du temps.",
        "improve.rt.big_up": "Les temps de reponse sont devenus nettement plus rapides, suggerant une amelioration de la vitesse de traitement et de la concentration.",
        "improve.rt.up": "Les temps de reponse se sont legerement ameliores, l'enfant reagissant un peu plus vite qu'avant.",
        "improve.rt.big_down": "Les temps de reponse ont ralenti par rapport aux sessions precedentes, ce qui pourrait indiquer une baisse de concentration ou une difficulte accrue.",
        "improve.rt.down": "Les temps de reponse ont legerement augmente.",
        "improve.rt.stable": "Les temps de reponse sont restes constants au fil du temps.",
        "improve.frustration.big_up": "Les signes de frustration ont diminue de facon significative. L'enfant semble beaucoup plus calme et a l'aise pendant le jeu.",
        "improve.frustration.up": "Les signes de frustration ont diminue, suggerant que l'enfant s'adapte bien aux activites.",
        "improve.frustration.big_down": "Les signes de frustration ont augmente, ce qui peut indiquer que l'enfant trouve les activites plus difficiles.",
        "improve.frustration.down": "Les signes de frustration ont legerement augmente.",
        "improve.frustration.stable": "Les niveaux de frustration sont restes a peu pres les memes.",
        "rec.specialist": "Envisagez de prendre rendez-vous avec un specialiste pour discuter des domaines ou l'enfant rencontre des difficultes. Cela ne signifie pas qu'il y a un probleme ; cela aide simplement a avoir une image plus claire.",
        "rec.breaks": "L'enfant a montre des signes de frustration pendant certaines activites. Essayez des sessions de jeu plus courtes avec des pauses entre les deux pour garder l'experience positive.",
        "rec.focus": "L'enfant pourrait beneficier d'activites de concentration a la maison, comme des puzzles, des jeux de construction ou des jeux de cartes simples qui necessitent de l'attention.",
        "rec.regress": "Certains scores ont baisse recemment. Cela peut arriver a cause de la fatigue, d'une journee difficile ou d'un besoin de pause. Gardez un oeil dessus au cours des prochaines semaines.",
        "rec.progress": "L'enfant montre une progression positive. Continuez avec les activites actuelles et maintenez des sessions regulieres.",
        "rec.check_back": "Revenez dans environ un mois pour voir comment les choses progressent.",
        "title": "Rapport de Performance Cognitive",
        "child_id": "Identifiant de l'enfant",
        "date": "Date",
        "report_type_label": "Type de rapport",
        "overview_header": "Vue d'ensemble",
        "games_header": "Observations par Jeu",
        "progress_header": "Progression au Fil du Temps",
        "recommendations_header": "Ce que Vous Pouvez Faire",
        "measures_label": "Ce que ca mesure",
        "progress_label": "Progression",
        "auto_generated": "Rapport de performance cognitive genere automatiquement par NeuroGames.",
    },
    "ar": {
        "report_type.progress": "تقرير التقدم",
        "report_type.assessment": "ملخص التقييم",
        "unknown": "غير معروف",
        "no_data": "لا توجد بيانات لعب كافية بعد لهذا الطفل.",
        "overview_total": "أكمل الطفل {sessions} جلسة لعب عبر {games} أنشطة.",
        "overview_strengths": "تظهر نقاط القوة الحالية بشكل أوضح في {items}.",
        "overview_challenges": "الأنشطة التي تحتاج إلى دعم أكبر هي {items}.",
        "overview_developing": "الأداء يتطور بشكل عام عبر الأنشطة المتاحة.",
        "no_detail_game": "لعب الطفل {game}، ولكن لا توجد بيانات كافية بعد لتقديم تفسير مفصل.",
        "not_enough_progress": "لا توجد جلسات كافية بعد لتقييم التقدم؛ تم لعب {sessions} جلسة فقط. نحتاج إلى 5 جلسات على الأقل.",
        "framing.large": "خلال آخر 10 جلسات مقارنة بالبداية الأولى للطفل",
        "framing.medium": "خلال الجلسات الأخيرة مقارنة بالجلسات السابقة",
        "framing.small": "منذ بداية لعب الطفل",
        "accuracy.excellent": "أجاب الطفل بشكل صحيح في معظم المحاولات تقريبًا، مما يدل على إتقان ممتاز لهذا النشاط.",
        "accuracy.good": "كان أداء الطفل جيدًا، حيث أجاب على معظم المحاولات بشكل صحيح وبثبات جيد.",
        "accuracy.moderate": "أجاب الطفل بشكل صحيح في مستوى مقبول، مع وجود مساحة واضحة للتحسن.",
        "accuracy.low": "واجه الطفل بعض الصعوبة في هذا النشاط، وقد يستفيد من تدريب إضافي.",
        "accuracy.very_low": "كان هذا النشاط صعبًا جدًا على الطفل، مع عدد كبير من الإجابات غير الصحيحة. هذا المجال يحتاج إلى متابعة ودعم أقرب.",
        "rt.very_fast": "استجاب الطفل بسرعة كبيرة، مما يشير إلى سرعة معالجة جيدة وانتباه مناسب.",
        "rt.normal": "كانت أوقات الاستجابة ضمن النطاق المتوقع، مما يدل على سرعة معالجة مناسبة.",
        "rt.slow": "احتاج الطفل إلى وقت أطول قليلًا للاستجابة، وقد يعني ذلك أنه يحتاج إلى وقت إضافي لمعالجة المعلومات.",
        "rt.delayed": "كانت أوقات الاستجابة بطيئة بشكل ملحوظ. قد يكون الطفل يواجه صعوبة في التركيز أو يحتاج إلى وقت أطول للتفكير.",
        "rt.very_delayed": "كانت استجابات الطفل بطيئة جدًا، وقد يشير ذلك إلى صعوبة في الحفاظ على الانتباه أثناء النشاط.",
        "frustration.low": "أظهر الطفل علامات قليلة جدًا من الإحباط أثناء اللعب، مما يدل على تجربة مريحة.",
        "frustration.medium": "ظهرت بعض علامات الإحباط. ربما شعر الطفل بالتحدي أو قلة الصبر في بعض اللحظات.",
        "frustration.high": "ظهرت علامات إحباط متكررة، وقد يعني ذلك أن النشاط كان صعبًا أو مرهقًا بالنسبة للطفل.",
        "severity.normal": "أداء الطفل في هذا المجال ضمن النطاق المتوقع، ولا توجد مؤشرات مقلقة حاليًا.",
        "severity.mild": "هناك مؤشرات بسيطة تستحق المتابعة. الأمر ليس مقلقًا، لكنه يحتاج إلى مراقبة خلال الأسابيع القادمة.",
        "severity.moderate": "يظهر الطفل بعض الصعوبات في هذا المجال وقد يستفيد من دعم موجه أو تدريب إضافي.",
        "severity.severe": "توجد تحديات واضحة في هذا المجال. قد يكون من المفيد إجراء تقييم أكثر تفصيلًا مع مختص.",
        "cluster.combined": "تشير أنماط لعب الطفل إلى أن تنظيم الانتباه والتحكم في الاندفاع مجالان يحتاجان إلى دعم، ويمكن أن يستفيدا من تنظيم واضح ومتابعة مستمرة.",
        "cluster.hyperactive": "تشير أنماط لعب الطفل إلى أنه قد يستفيد من أنشطة تقوي التحكم في الاندفاع ومهارات الانتظار، بينما تبدو مهارات الانتباه لديه أقوى نسبيًا.",
        "cluster.inattentive": "تشير أنماط لعب الطفل إلى أنه قد يستفيد من أنشطة تقوي التركيز المستمر، بينما يبدو التحكم في الاندفاع لديه أقوى نسبيًا.",
        "cluster.optimal": "أنماط لعب الطفل ضمن النطاق المتوقع، ولا تظهر مؤشرات واضحة على صعوبات كبيرة في الانتباه أو التحكم في الاندفاع.",
        "improve.correct.big_up": "تحسنت الدقة بشكل واضح. أصبح الطفل يقدم إجابات صحيحة أكثر مقارنة بالجلسات الأولى.",
        "improve.correct.up": "تحسنت الدقة. أصبح الطفل يجيب بشكل صحيح أكثر من السابق.",
        "improve.correct.big_down": "انخفضت الدقة مقارنة بالجلسات السابقة، وقد يكون ذلك مرتبطًا بالتعب أو قلة التفاعل أو ارتفاع مستوى الصعوبة.",
        "improve.correct.down": "انخفضت الدقة قليلًا مقارنة بالجلسات السابقة.",
        "improve.correct.stable": "بقيت الدقة مستقرة مع مرور الوقت.",
        "improve.rt.big_up": "أصبحت أوقات الاستجابة أسرع بشكل ملحوظ، مما يشير إلى تحسن في سرعة المعالجة والتركيز.",
        "improve.rt.up": "تحسنت أوقات الاستجابة قليلًا، وأصبح الطفل يتفاعل بسرعة أكبر من قبل.",
        "improve.rt.big_down": "أصبحت أوقات الاستجابة أبطأ مقارنة بالجلسات السابقة، وقد يشير ذلك إلى انخفاض التركيز أو زيادة الصعوبة.",
        "improve.rt.down": "زادت أوقات الاستجابة قليلًا.",
        "improve.rt.stable": "بقيت أوقات الاستجابة مستقرة تقريبًا مع مرور الوقت.",
        "improve.frustration.big_up": "انخفضت علامات الإحباط بشكل واضح. يبدو الطفل أكثر هدوءًا وراحة أثناء اللعب.",
        "improve.frustration.up": "انخفضت علامات الإحباط، مما يشير إلى أن الطفل يتأقلم بشكل أفضل مع الأنشطة.",
        "improve.frustration.big_down": "زادت علامات الإحباط، وقد يعني ذلك أن الطفل يجد الأنشطة أكثر صعوبة.",
        "improve.frustration.down": "زادت علامات الإحباط قليلًا.",
        "improve.frustration.stable": "بقي مستوى الإحباط قريبًا من السابق.",
        "rec.specialist": "يمكن التفكير في لقاء مع مختص لمناقشة المجالات التي يواجه فيها الطفل صعوبة. هذا لا يعني وجود مشكلة مؤكدة، بل يساعد على تكوين صورة أوضح.",
        "rec.breaks": "أظهر الطفل علامات إحباط في بعض الأنشطة. من الأفضل تجربة جلسات أقصر مع فواصل منتظمة للحفاظ على تجربة إيجابية.",
        "rec.focus": "قد يستفيد الطفل من أنشطة منزلية تقوي التركيز، مثل الألغاز أو المكعبات أو ألعاب البطاقات البسيطة التي تتطلب الانتباه.",
        "rec.regress": "انخفضت بعض النتائج مؤخرًا. قد يحدث ذلك بسبب التعب أو يوم صعب أو الحاجة إلى راحة. من الأفضل متابعة الأمر خلال الأسابيع القادمة.",
        "rec.progress": "يظهر الطفل تقدمًا إيجابيًا. من الأفضل الاستمرار في الأنشطة الحالية مع الحفاظ على جلسات منتظمة.",
        "rec.check_back": "من المفيد مراجعة التقدم مرة أخرى بعد حوالي شهر.",
        "title": "تقرير الأداء المعرفي",
        "child_id": "معرّف الطفل",
        "date": "التاريخ",
        "report_type_label": "نوع التقرير",
        "overview_header": "نظرة عامة",
        "games_header": "ملاحظات حسب كل لعبة",
        "progress_header": "التقدم عبر الوقت",
        "recommendations_header": "ما يمكنكم فعله",
        "measures_label": "ما يقيسه",
        "progress_label": "التقدم",
        "auto_generated": "تقرير الأداء المعرفي تم إنشاؤه تلقائيًا بواسطة NeuroGames.",
    },
}

# Add section header keys to English copy
REPORT_COPY["en"].update({
    "title": "Cognitive Performance Report",
    "child_id": "Child ID",
    "date": "Date",
    "report_type_label": "Report Type",
    "overview_header": "Overview",
    "games_header": "Game-by-Game Observations",
    "progress_header": "Progress Over Time",
    "recommendations_header": "What You Can Do",
    "measures_label": "What it measures",
    "progress_label": "Progress",
    "auto_generated": "Auto-generated cognitive performance report by NeuroGames.",
})


def _copy(locale: str | None, key: str, **params) -> str:
    locale = normalize_report_locale(locale)
    template = REPORT_COPY.get(locale, {}).get(key) or REPORT_COPY["en"].get(key, key)
    return template.format(**params) if params else template


def _registered_report_game_ids() -> list[str]:
    """Return registered game modules for report context, with built-in fallback."""
    try:
        from mlops.db import list_game_modules
        games = [g["game_id"] for g in list_game_modules(active_only=False)]
        return games or list(GAME_NAMES)
    except Exception:
        return list(GAME_NAMES)


def _game_domain(game: str, locale: str | None = None) -> str:
    locale = normalize_report_locale(locale)
    return GAME_DOMAINS_I18N.get(locale, GAME_DOMAINS).get(game, GAME_DOMAINS.get(game, "Unknown"))


def _game_display_name(game: str, locale: str | None = None) -> str:
    locale = normalize_report_locale(locale)
    return GAME_DISPLAY_NAMES_I18N.get(locale, GAME_DISPLAY_NAMES).get(game, GAME_DISPLAY_NAMES.get(game, game.capitalize()))


def _cluster_label(cluster: str, locale: str | None = None) -> str:
    locale = normalize_report_locale(locale)
    if locale != "ar":
        return cluster or _copy(locale, "unknown")
    if "Combined" in cluster:
        return "نمط مختلط: انتباه واندفاع"
    if "Hyperactive" in cluster:
        return "نمط اندفاعي / فرط حركة"
    if "Inattentive" in cluster:
        return "نمط ضعف الانتباه"
    if "Optimal" in cluster or "Neurotypical" in cluster:
        return "نمط ضمن النطاق المتوقع"
    return _copy(locale, "unknown")

# Friendly names for ML module types
MODULE_FRIENDLY = {
    "Classification": "Learning Profile Detection",
    "Sequence Model": "Behavioral Pattern Analysis",
    "Assessment": "Session-Level Evaluation",
    "Anomaly Detection": "Unusual Pattern Screening",
    "Cross-Game": "Combined Multi-Game Analysis",
}

# Game-specific fields to extract (beyond the shared base)
_GAME_SPECIFIC_FIELDS = {
    "gonogo": [
        "hits", "misses", "false_alarms", "correct_rejections",
        "commission_error_rate", "omission_error_rate", "d_prime_approx",
        "rt_variability_ms",
    ],
    "memory": [
        "pairs_found", "total_pairs", "total_attempts",
        "match_accuracy", "completion_time_s",
    ],
    "tracking": [
        "hits", "misses", "hit_rate", "avg_rt_ms", "rt_std_ms",
        "avg_target_speed", "avg_distractor_count",
    ],
    "shapes": [
        "levels_played", "levels_won", "total_moves",
        "session_accuracy", "avg_move_efficiency", "avg_time_per_level",
        "avg_rotations",
    ],
    "puzzle": [
        "levels_completed", "avg_mastery_index", "avg_planning_score",
        "avg_impulsivity_score", "avg_attention_score",
        "avg_frustration_score", "avg_time_score",
    ],
}


# ── Cluster label mapping ────────────────────────────────────────────────────

def _map_cluster_label(raw_cluster) -> str:
    """Convert a raw cluster value (int, str-int, or label) to a readable label."""
    if raw_cluster is None or str(raw_cluster).strip() == "":
        return "Unknown"
    raw = str(raw_cluster).strip()
    # Already a label?
    if raw in CLUSTER_LABELS:
        return raw
    # Numeric index?
    try:
        idx = int(float(raw))
        if 0 <= idx < len(CLUSTER_LABELS):
            return CLUSTER_LABELS[idx]
    except (ValueError, TypeError):
        pass
    return raw


# ── Severity classification ──────────────────────────────────────────────────

def _classify_severity(stats: dict) -> str:
    """
    Determine a severity label from aggregate stats.
    Returns: 'normal', 'mild', 'moderate', or 'severe'.
    """
    signals = 0

    # High frustration → impulsivity concern
    frust = stats.get("avg_frustration_clicks", 0)
    if frust >= 10:
        signals += 2
    elif frust >= 4:
        signals += 1

    # Low accuracy
    total = stats.get("avg_correct", 0) + stats.get("avg_incorrect", 0)
    if total > 0:
        acc = stats["avg_correct"] / total
        if acc < 0.50:
            signals += 2
        elif acc < 0.70:
            signals += 1

    # Slow reaction time (>1.5s is concerning for children's games)
    rt = stats.get("avg_rt", 0)
    if rt > 2.0:
        signals += 2
    elif rt > 1.5:
        signals += 1

    # Cluster-based
    cluster = stats.get("clinical_cluster", "Unknown")
    if "Combined" in cluster or "Hyperactive" in cluster:
        signals += 2
    elif "Inattentive" in cluster:
        signals += 1

    if signals >= 5:
        return "severe"
    if signals >= 3:
        return "moderate"
    if signals >= 1:
        return "mild"
    return "normal"


def _accuracy_to_level(value: float, metric_type: str = "accuracy") -> tuple:
    """
    Convert a metric value into (label, emoji, explanation).
    metric_type: 'accuracy' (0-1), 'reaction_time' (seconds), or 'count'
    """
    if value is None:
        return ("N/A", "⚪", "Data not available.")

    if metric_type == "accuracy":
        if value >= 0.95:
            return ("Excellent", "🟢", "Well above average performance.")
        if value >= 0.85:
            return ("Good", "🟢", "Solid performance within expected range.")
        if value >= 0.70:
            return ("Moderate", "🟡", "Acceptable but shows room for improvement.")
        if value >= 0.50:
            return ("Below Average", "🟠", "Below typical levels — may need attention.")
        return ("Needs Attention", "🔴", "Significantly below expected — further evaluation recommended.")

    if metric_type == "reaction_time":
        if value <= 0.4:
            return ("Very Fast", "🟢", "Exceptionally quick responses.")
        if value <= 0.7:
            return ("Normal", "🟢", "Within expected reaction time range.")
        if value <= 1.2:
            return ("Slow", "🟡", "Slightly elevated reaction times.")
        if value <= 2.0:
            return ("Delayed", "🟠", "Noticeably slower responses — may indicate inattention.")
        return ("Very Delayed", "🔴", "Reaction times significantly above expected range.")

    # Default: count-based (e.g. frustration clicks)
    return ("", "📊", "")


# ── Narrative Interpretation Helpers ─────────────────────────────────────────

def _interpret_accuracy(rate: float | None, locale: str | None = None) -> str:
    """Translate accuracy rate (0-1) into a parent-friendly sentence."""
    if rate is None:
        return ""
    if rate >= 0.95:
        return _copy(locale, "accuracy.excellent")
    if rate >= 0.85:
        return _copy(locale, "accuracy.good")
    if rate >= 0.70:
        return _copy(locale, "accuracy.moderate")
    if rate >= 0.50:
        return _copy(locale, "accuracy.low")
    return _copy(locale, "accuracy.very_low")


def _interpret_reaction_time(rt_seconds: float | None, locale: str | None = None) -> str:
    """Translate average reaction time into a readable sentence."""
    if rt_seconds is None:
        return ""
    if rt_seconds <= 0.4:
        return _copy(locale, "rt.very_fast")
    if rt_seconds <= 0.7:
        return _copy(locale, "rt.normal")
    if rt_seconds <= 1.2:
        return _copy(locale, "rt.slow")
    if rt_seconds <= 2.0:
        return _copy(locale, "rt.delayed")
    return _copy(locale, "rt.very_delayed")


def _interpret_frustration(clicks: float | None, locale: str | None = None) -> str:
    """Translate frustration click count into a readable sentence."""
    if clicks is None or clicks <= 0:
        return ""
    if clicks < 3:
        return _copy(locale, "frustration.low")
    if clicks < 7:
        return _copy(locale, "frustration.medium")
    return _copy(locale, "frustration.high")


def _interpret_severity(level: str, locale: str | None = None) -> str:
    """Translate severity level into a parent-friendly description."""
    mapping = {
        "normal": "The child's performance in this area is within the expected range — no concerns at this time.",
        "mild": "There are minor signs that deserve monitoring. Nothing alarming, but worth keeping an eye on over the coming weeks.",
        "moderate": "The child shows some difficulties in this area that may benefit from targeted support or additional practice.",
        "severe": "There are significant challenges in this area. A more detailed assessment by a specialist may be helpful.",
    }
    if locale:
        return _copy(locale, f"severity.{level}") if level in mapping else ""
    return mapping.get(level, "")


def _interpret_cluster(cluster: str, locale: str | None = None) -> str:
    """Translate behavioral cluster label into a parent-friendly description."""
    if "Combined" in cluster:
        return _copy(locale, "cluster.combined") if locale else "The child's play patterns suggest both attention regulation and impulse management are areas for growth, which is a common profile that responds well to structured support."
    if "Hyperactive" in cluster:
        return _copy(locale, "cluster.hyperactive") if locale else "The child's play patterns suggest they may benefit from activities that build impulse management and waiting skills, while their attention skills appear relatively strong."
    if "Inattentive" in cluster:
        return _copy(locale, "cluster.inattentive") if locale else "The child's play patterns suggest they may benefit from activities that strengthen sustained focus, while their impulse management appears relatively strong."
    if "Optimal" in cluster or "Neurotypical" in cluster:
        return _copy(locale, "cluster.optimal") if locale else "The child's play patterns are within the typical range, with no significant attention or impulse management concerns identified."
    return ""


def _interpret_improvement(
    metric_name: str,
    baseline: float,
    current: float,
    delta_pct: float,
    locale: str | None = None,
) -> str:
    """Translate an improvement metric into a readable sentence."""
    if metric_name == "correct":
        if delta_pct > 10:
            if locale:
                return _copy(locale, "improve.correct.big_up")
            return "Accuracy has improved significantly — the child is getting noticeably more answers correct compared to earlier sessions."
        if delta_pct > 3:
            if locale:
                return _copy(locale, "improve.correct.up")
            return "Accuracy has improved — the child is getting more answers correct than before."
        if delta_pct < -10:
            if locale:
                return _copy(locale, "improve.correct.big_down")
            return "Accuracy has decreased compared to earlier sessions, which may indicate fatigue, disengagement, or increased difficulty."
        if delta_pct < -3:
            if locale:
                return _copy(locale, "improve.correct.down")
            return "Accuracy has dipped slightly compared to earlier sessions."
        if locale:
            return _copy(locale, "improve.correct.stable")
        return "Accuracy has remained stable over time."

    if metric_name == "rt":
        if delta_pct < -10:
            if locale:
                return _copy(locale, "improve.rt.big_up")
            return "Response times have gotten noticeably faster, suggesting improved processing speed and focus."
        if delta_pct < -3:
            if locale:
                return _copy(locale, "improve.rt.up")
            return "Response times have improved slightly, with the child reacting a bit faster than before."
        if delta_pct > 10:
            if locale:
                return _copy(locale, "improve.rt.big_down")
            return "Response times have slowed down compared to earlier sessions, which could indicate reduced focus or increased difficulty."
        if delta_pct > 3:
            if locale:
                return _copy(locale, "improve.rt.down")
            return "Response times have increased slightly."
        if locale:
            return _copy(locale, "improve.rt.stable")
        return "Response times have remained consistent over time."

    if metric_name == "frustration":
        if delta_pct < -15:
            if locale:
                return _copy(locale, "improve.frustration.big_up")
            return "Frustration events have decreased significantly — the child appears much calmer and more comfortable during gameplay."
        if delta_pct < -5:
            if locale:
                return _copy(locale, "improve.frustration.up")
            return "Frustration events have decreased, suggesting the child is adapting well to the activities."
        if delta_pct > 15:
            if locale:
                return _copy(locale, "improve.frustration.big_down")
            return "Frustration events have increased, which may indicate the child is finding the activities more challenging."
        if delta_pct > 5:
            if locale:
                return _copy(locale, "improve.frustration.down")
            return "Frustration events have increased slightly."
        if locale:
            return _copy(locale, "improve.frustration.stable")
        return "Frustration levels have remained about the same."

    if metric_name == "time_spent":
        if delta_pct > 10:
            return "The child is spending more time on sessions, which could indicate deeper engagement or increased difficulty."
        if delta_pct < -10:
            return "Sessions are shorter than before, which may indicate improved efficiency or reduced engagement."
        return "Session duration has remained about the same."

    return ""


def _get_comparison_windows(n_sessions: int) -> tuple:
    """
    Adaptive 3-tier comparison: returns (n_recent, n_baseline) or (0, 0) if not enough data.
    - 6-14 sessions:  last 3 vs first 3
    - 15-29 sessions: last 5 vs first 10
    - 30+ sessions:   last 10 vs first 20
    - < 6 sessions:   no comparison
    """
    if n_sessions >= 30:
        return (10, 20)
    if n_sessions >= 15:
        return (5, 10)
    if n_sessions >= 6:
        return (3, 3)
    return (0, 0)


def _get_comparison_framing(n_sessions: int, locale: str | None = None) -> str:
    """Return the narrative framing for the comparison based on session count."""
    if n_sessions >= 30:
        if locale:
            return _copy(locale, "framing.large")
        return "Over the last 10 sessions compared to the child's initial baseline"
    if n_sessions >= 15:
        if locale:
            return _copy(locale, "framing.medium")
        return "Over the past few sessions compared to earlier"
    if n_sessions >= 6:
        if locale:
            return _copy(locale, "framing.small")
        return "Since the child started"
    return ""


# ── Context Builder ──────────────────────────────────────────────────────────

def build_context(
    participant_id: str,
    pipeline_summary: list = None,
    compare: bool = False,
    limit: int = 50,
    locale: str = None,
    snapshot_until: str = None,
    generated_at: str = None,
) -> dict:
    """
    Build a comprehensive structured context dict.

    If participant_id is "pipeline_run", uses global pipeline_summary.
    Otherwise, reads individual session data from PostgreSQL with full feature extraction.
    """
    locale = normalize_report_locale(locale)
    generated_at = generated_at or datetime.now().isoformat()
    snapshot_until = snapshot_until or generated_at

    context = {
        "participant_id": participant_id,
        "generated_at": generated_at,
        "locale": locale,
        "snapshot_until": snapshot_until,
        "is_individual": participant_id != "pipeline_run",
        "has_comparison": compare,
        "games": {},
    }

    if not context["is_individual"]:
        # GLOBAL PIPELINE SUMMARY MODE
        pipeline_summary = pipeline_summary or []
        for entry in pipeline_summary:
            game = entry.get("game", "unknown")
            module = entry.get("module", "unknown")
            if game not in context["games"]:
                context["games"][game] = {
                    "cognitive_domain": GAME_DOMAINS.get(game, "Unknown"),
                    "modules": {},
                }
            context["games"][game]["modules"][module] = {
                "accuracy": entry.get("test_acc"),
                "elapsed_seconds": round(entry.get("elapsed", 0), 2),
                "anomalies_flagged": entry.get("n_flagged"),
            }
        return context

    # ── INDIVIDUAL PARTICIPANT MODE ──────────────────────────────────────
    # Uses SQL-level windowing: baseline snapshot + recent sessions only.
    # NEVER loads the full session history into memory.

    from mlops.db import (
        count_participant_sessions, ensure_baseline,
        load_recent_sessions,
    )

    RECENT_WINDOW = 10  # last N sessions for "current" metrics

    for game in _registered_report_game_ids():
        try:
            # 1. Session count via SQL COUNT(*) — O(1)
            n_total = count_participant_sessions(
                participant_id,
                game,
                until_iso=snapshot_until,
            )
            if n_total == 0:
                continue

            # 2. Baseline snapshot (write-once, from first 5 sessions)
            baseline = ensure_baseline(participant_id, game, min_sessions=5)

            # 3. Recent sessions via SQL LIMIT — never loads full history
            recent_df = load_recent_sessions(
                participant_id,
                game,
                limit=RECENT_WINDOW,
                until_iso=snapshot_until,
            )
            if recent_df.empty:
                continue

            stats = {
                "cognitive_domain": _game_domain(game, locale),
                "total_sessions": n_total,
            }

            # ── Current metrics (from recent window only) ────────────────
            _safe_mean = lambda col: round(float(recent_df[col].mean()), 2) if col in recent_df.columns and recent_df[col].notna().any() else None

            current = {
                "avg_correct": _safe_mean("Correct_Responses"),
                "avg_incorrect": _safe_mean("Incorrect_Responses"),
                "avg_rt": _safe_mean("Reaction_Time"),
                "avg_time_spent": _safe_mean("Time_Spent"),
                "avg_total_actions": _safe_mean("Total_Actions"),
                "avg_frustration_clicks": _safe_mean("frustration_clicks"),
            }
            if game == "puzzle":
                current["avg_hint_usage"] = _safe_mean("Hint_Usage")

            # Derived accuracy from current window
            if current["avg_correct"] is not None and current["avg_incorrect"] is not None:
                total_resp = current["avg_correct"] + current["avg_incorrect"]
                current["accuracy_rate"] = round(current["avg_correct"] / max(0.01, total_resp), 2)
                current["error_rate"] = round(current["avg_incorrect"] / max(0.01, total_resp), 2)

            stats["current"] = current
            # Flat compat keys for severity & narrative helpers
            stats["avg_correct"] = current["avg_correct"]
            stats["avg_incorrect"] = current["avg_incorrect"]
            stats["avg_rt"] = current["avg_rt"]
            stats["avg_frustration_clicks"] = current.get("avg_frustration_clicks")
            stats["accuracy_rate"] = current.get("accuracy_rate")

            # ── Baseline snapshot ────────────────────────────────────────
            if baseline:
                stats["baseline"] = baseline

                # ── Trend (current vs baseline) ──────────────────────────
                trend = {}
                for metric_key in ["avg_correct", "avg_rt", "avg_frustration_clicks"]:
                    base_val = baseline.get(metric_key)
                    curr_val = current.get(metric_key)
                    if base_val is not None and curr_val is not None and base_val != 0:
                        delta = round(curr_val - base_val, 3)
                        pct = round(delta / abs(base_val) * 100, 1)
                        trend[metric_key] = {
                            "baseline": base_val,
                            "current": curr_val,
                            "delta": delta,
                            "delta_pct": pct,
                        }

                # Accuracy trend (derived)
                base_acc = baseline.get("accuracy_rate")
                curr_acc = current.get("accuracy_rate")
                if base_acc is not None and curr_acc is not None:
                    delta_acc = round(curr_acc - base_acc, 3)
                    pct_acc = round(delta_acc / max(0.01, abs(base_acc)) * 100, 1)
                    trend["accuracy_rate"] = {
                        "baseline": base_acc,
                        "current": curr_acc,
                        "delta": delta_acc,
                        "delta_pct": pct_acc,
                    }

                if trend:
                    stats["trend"] = trend

            # Max level reached (from recent window)
            if "max_level_reached" in recent_df.columns and recent_df["max_level_reached"].notna().any():
                stats["highest_level_reached"] = int(recent_df["max_level_reached"].max())

            # Rounds info (from recent window)
            if "rounds_played" in recent_df.columns and recent_df["rounds_played"].notna().any():
                total_played = int(recent_df["rounds_played"].sum())
                total_passed = int(recent_df["rounds_passed"].sum()) if "rounds_passed" in recent_df.columns else 0
                stats["total_rounds_played"] = total_played
                stats["total_rounds_passed"] = total_passed
                stats["round_pass_rate"] = round(total_passed / max(1, total_played), 2)

            # Performance level (most recent)
            if "Performance_Level" in recent_df.columns:
                stats["current_performance"] = str(recent_df["Performance_Level"].iloc[-1])

            # Behavioral cluster (mode across recent sessions)
            if "cluster" in recent_df.columns and recent_df["cluster"].notna().any():
                raw_cluster = recent_df["cluster"].mode().iloc[0] if not recent_df["cluster"].mode().empty else "Unknown"
                stats["clinical_cluster"] = _map_cluster_label(raw_cluster)

            # ── Game-specific metrics (from recent window) ───────────────
            specific_fields = _GAME_SPECIFIC_FIELDS.get(game, [])
            game_specific = {}
            for field in specific_fields:
                if field in recent_df.columns and recent_df[field].notna().any():
                    val = recent_df[field].mean()
                    game_specific[field] = round(float(val), 3)
            if game_specific:
                stats["game_metrics"] = game_specific

            # Severity classification (uses current metrics)
            stats["severity"] = _classify_severity(stats)

            # ── Narrative interpretation ──────────────────────────────────
            narrative_parts = []
            display_name = _game_display_name(game, locale)

            # Core performance narrative (from current window)
            acc_narrative = _interpret_accuracy(stats.get("accuracy_rate"), locale)
            if acc_narrative:
                narrative_parts.append(acc_narrative)

            rt_narrative = _interpret_reaction_time(stats.get("avg_rt"), locale)
            if rt_narrative:
                narrative_parts.append(rt_narrative)

            frust_narrative = _interpret_frustration(stats.get("avg_frustration_clicks"), locale)
            if frust_narrative:
                narrative_parts.append(frust_narrative)

            sev_narrative = _interpret_severity(stats.get("severity", "normal"), locale)
            if sev_narrative:
                narrative_parts.append(sev_narrative)

            stats["narrative"] = " ".join(narrative_parts) if narrative_parts else _copy(locale, "no_detail_game", game=display_name)

            # Cluster narrative
            cluster_label = stats.get("clinical_cluster", "Unknown")
            if cluster_label != "Unknown":
                stats["cluster_narrative"] = _interpret_cluster(cluster_label, locale)

            # ── Improvement narrative (always, using baseline vs current) ─
            if baseline and stats.get("trend"):
                improvement_narratives = []
                trend = stats["trend"]

                mapping = {
                    "avg_correct": "correct",
                    "avg_rt": "rt",
                    "avg_frustration_clicks": "frustration",
                }
                for trend_key, narr_key in mapping.items():
                    if trend_key in trend:
                        t = trend[trend_key]
                        sentence = _interpret_improvement(narr_key, t["baseline"], t["current"], t["delta_pct"], locale)
                        if sentence:
                            improvement_narratives.append(sentence)

                if improvement_narratives:
                    framing = _get_comparison_framing(n_total, locale)
                    stats["improvement_narrative"] = f"{framing}: " + " ".join(improvement_narratives) if framing else " ".join(improvement_narratives)
                    stats["comparison_window"] = {
                        "recent_sessions": min(n_total, RECENT_WINDOW),
                        "baseline_sessions": 5,
                        "total_sessions": n_total,
                    }

            elif n_total < 5:
                stats["improvement_narrative"] = _copy(locale, "not_enough_progress", sessions=n_total)

            context["games"][game] = stats

        except Exception as e:
            log.warning("Error processing %s for %s: %s", game, participant_id, e)

    if not context["games"]:
        log.warning("Participant '%s' not found in any PostgreSQL sessions.", participant_id)

    context["session_count_at_snapshot"] = sum(
        stats.get("total_sessions", 0)
        for stats in context["games"].values()
    )

    return context


# ── Structured Output Schema ────────────────────────────────────────────────

_OUTPUT_SCHEMA = {
    "overview": "A 3-4 sentence paragraph summarizing the child's overall cognitive profile in plain, parent-friendly language. No technical terms or raw numbers.",
    "per_game_narrative": [
        {
            "game": "game_name",
            "narrative": "A 2-3 sentence paragraph describing how the child performed in this game, written for parents and teachers. Use everyday language."
        }
    ],
    "improvement_narrative": "A paragraph describing the child's progress over time, comparing recent performance to earlier sessions. Only include if comparison data is available in the input.",
    "recommendations": ["A plain-language, actionable recommendation that a parent or teacher can follow"],
}

_FEW_SHOT_EXAMPLE = {
    "overview": "Overall, the child shows a solid ability to control impulses and make quick decisions, which is a real strength. However, remembering and matching information was more challenging, and the child sometimes became frustrated during those activities. This pattern suggests the child may benefit from exercises that strengthen memory and focus.",
    "per_game_narrative": [
        {
            "game": "gonogo",
            "narrative": "In the Go/No-Go game, which tests the ability to hold back and think before acting, the child did very well. They responded quickly and accurately, rarely pressing the button when they shouldn't have. This shows good self-control for their age.",
        },
        {
            "game": "memory",
            "narrative": "In the Memory Match game, the child had some difficulty finding and remembering matching pairs. They needed several attempts and showed signs of frustration during the activity. This suggests working memory — the ability to hold information in mind — is an area that could use some extra support.",
        },
    ],
    "improvement_narrative": "Since the child started playing, their accuracy has improved in most games, and they are responding a bit faster than before. Frustration events have also decreased, suggesting they are becoming more comfortable with the activities.",
    "recommendations": [
        "Try simple memory games at home a few times a week, like card matching or 'What's missing?' activities",
        "Check in again in about a month to see if the memory improvements continue",
        "If the child seems frustrated during an activity, take a short break — this helps reset focus",
    ],
}


# ── LLM Prompt ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are writing a game-based cognitive performance report for the NeuroGames platform. Your audience is PARENTS, TEACHERS, and THERAPISTS — NOT doctors or researchers.

IMPORTANT: This report is DESCRIPTIVE, not diagnostic. It describes game-based cognitive performance, progress, and practical recommendations. It does NOT diagnose any medical condition.

NeuroGames uses ADHD game modules. The five built-in reference modules are Go/No-Go, Memory Match, Visual Tracking, Shape Builder, and Puzzle Quest, and compatible ADHD modules may be added through the game-module registry.

IMPORTANT: Only use game keys that appear in the input data. Do NOT invent or rename game keys.

TASK:
The data you receive contains TWO temporal windows for each game:
- "baseline": metrics from the child's FIRST sessions (their starting point)
- "current": metrics from their MOST RECENT sessions (where they are now)
- "trend": the change between baseline and current (positive = improvement)

Your job is to describe the child's LEARNING CURVE — how they have adapted and grown since they started. Focus on the journey, not a flat summary.

CRITICAL RULES:
1. Write in PLAIN LANGUAGE — no technical terms like "d-prime", "commission error rate", "delta", or "standard deviation"
2. NEVER show raw numbers, percentages, or metrics. Translate everything into everyday words.
3. Focus on GROWTH and ADAPTATION — "The child started with difficulty but has shown clear improvement" is better than "Average accuracy is 60%"
4. Be warm and supportive in tone — these are children, and parents need reassurance alongside honest assessment.
5. Use the baseline vs. current comparison to tell a story of progress.
6. Use the "narrative" and "improvement_narrative" fields from the input as guidance.
7. Do NOT add signatures, greetings, or pleasantries.
8. In per_game_narrative, the "game" field MUST be one of the game keys present in the input data
9. NEVER say the child "has" a condition. Use descriptive language like "shows patterns consistent with..." or "may benefit from..."

Respond with ONLY valid JSON. No markdown, no code fences, no explanation outside the JSON."""

_USER_PROMPT_TEMPLATE = """Here is the child's gameplay data with narrative interpretations:

{context_json}

Write the report as JSON matching this schema:
{schema}

Here is an example of good narrative writing for a DIFFERENT child (do NOT copy these values):
{example}

Now write the report for the child with ID {participant_id}. Remember: use PLAIN LANGUAGE, no raw numbers or technical terms."""


def _build_prompt(context: dict) -> tuple:
    """Build the (system, user) prompt pair for the LLM."""
    context_json = json.dumps(context, indent=2, default=str)
    schema_json = json.dumps(_OUTPUT_SCHEMA, indent=2)
    example_json = json.dumps(_FEW_SHOT_EXAMPLE, indent=2)
    locale = normalize_report_locale(context.get("locale"))
    language_instruction = REPORT_LOCALE_INSTRUCTIONS[locale]

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        context_json=context_json,
        schema=schema_json,
        example=example_json,
        participant_id=context["participant_id"],
    )
    system_prompt = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"LANGUAGE REQUIREMENT:\n"
        f"- {language_instruction}\n"
        f"- Keep JSON keys, game keys, and schema field names exactly as specified.\n"
        f"- Translate only human-readable string values."
    )
    return system_prompt, user_prompt


# ── LLM API Calls (with retry) ───────────────────────────────────────────────

def _extract_gemini_text(data: dict) -> str:
    """Extract text from Gemini generateContent-style responses."""
    if isinstance(data.get("text"), str):
        return data["text"].strip()
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()

    chunks = []
    for candidate in data.get("candidates", []) or []:
        content = candidate.get("content") or {}
        for part in content.get("parts", []) or []:
            text = part.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _gemini_model_candidates(model: str = None) -> list[str]:
    """Return primary and fallback Gemini models without duplicates."""
    candidates = [model or GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]
    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def call_gemini(system_prompt: str, user_prompt: str, model: str = None) -> str | None:
    """
    Send a prompt to the Gemini API and return generated JSON text.
    Returns None on any provider problem so the template report can be used.
    """
    if not HAS_HTTPX:
        log.warning("httpx not installed - Gemini generation unavailable.")
        return None

    if not GEMINI_API_KEY:
        log.info("Gemini API key is not configured. Falling back to template.")
        return None

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": LLM_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }
    if GEMINI_THINKING_BUDGET:
        payload["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": int(GEMINI_THINKING_BUDGET),
        }

    models = _gemini_model_candidates(model)
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        for model_index, current_model in enumerate(models):
            url = f"{GEMINI_API_BASE.rstrip('/')}/models/{current_model}:generateContent"
            has_fallback = model_index < len(models) - 1

            for attempt in range(1 + LLM_MAX_RETRIES):
                try:
                    t0 = time.time()
                    response = client.post(url, params={"key": GEMINI_API_KEY}, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    raw = _extract_gemini_text(data)
                    elapsed = round(time.time() - t0, 1)
                    log.info(
                        "Gemini model %s responded in %ss (%d chars, attempt %d)",
                        current_model,
                        elapsed,
                        len(raw),
                        attempt + 1,
                    )
                    finish_reason = next(
                        (candidate.get("finishReason") for candidate in data.get("candidates", []) if candidate.get("finishReason")),
                        None,
                    )
                    if finish_reason == "MAX_TOKENS":
                        log.warning("Gemini model %s stopped because maxOutputTokens was reached.", current_model)
                    return raw

                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    wait = 3 * (2 ** attempt)
                    log.warning(
                        "Gemini model %s attempt %d failed (%s). Retrying in %ds...",
                        current_model,
                        attempt + 1,
                        e.__class__.__name__,
                        wait,
                    )
                    if attempt < LLM_MAX_RETRIES:
                        time.sleep(wait)
                    elif has_fallback:
                        log.warning("Gemini model %s unavailable. Trying fallback model %s.", current_model, models[model_index + 1])
                        break
                    else:
                        log.error("All Gemini models failed. Falling back to template.")
                        return None

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    retryable = status in {429, 500, 502, 503, 504}
                    overloaded = status in {429, 503}
                    if overloaded and has_fallback:
                        log.warning(
                            "Gemini model %s HTTP %s. Trying fallback model %s.",
                            current_model,
                            status,
                            models[model_index + 1],
                        )
                        break
                    if retryable and attempt < LLM_MAX_RETRIES:
                        wait = 3 * (2 ** attempt)
                        log.warning("Gemini model %s HTTP %s. Retrying in %ds...", current_model, status, wait)
                        time.sleep(wait)
                        continue
                    if has_fallback:
                        log.warning(
                            "Gemini model %s HTTP error %s. Trying fallback model %s.",
                            current_model,
                            status,
                            models[model_index + 1],
                        )
                        break
                    log.error("Gemini HTTP error %s: %s", status, e.response.text[:200])
                    return None

                except Exception as e:
                    if has_fallback:
                        log.warning("Unexpected error calling Gemini model %s: %s. Trying fallback model %s.", current_model, e, models[model_index + 1])
                        break
                    log.error("Unexpected error calling Gemini: %s", e)
                    return None

    return None


def call_llm(system_prompt: str, user_prompt: str) -> str | None:
    """Call the configured LLM provider, or return None for template fallback."""
    if LLM_PROVIDER in {"gemini", "google", "google-gemini"}:
        return call_gemini(system_prompt, user_prompt)
    if LLM_PROVIDER == "ollama":
        return call_ollama(system_prompt, user_prompt)
    if LLM_PROVIDER in {"template", "none", "off", "disabled"}:
        log.info("LLM provider disabled. Using template report.")
        return None

    log.warning("Unknown LLM_PROVIDER '%s'. Falling back to template.", LLM_PROVIDER)
    return None


def call_ollama(system_prompt: str, user_prompt: str, model: str = None) -> str | None:
    """
    Send a prompt to the Ollama local LLM and return the generated text.
    Retries up to OLLAMA_MAX_RETRIES times with exponential backoff.
    Returns None if Ollama is unavailable or all retries fail.
    """
    if not HAS_HTTPX:
        log.warning("httpx not installed — LLM generation unavailable.")
        return None

    model = model or OLLAMA_MODEL
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": LLM_MAX_OUTPUT_TOKENS,
        },
    }

    for attempt in range(1 + OLLAMA_MAX_RETRIES):
        try:
            t0 = time.time()
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                raw = data.get("response", "").strip()
                elapsed = round(time.time() - t0, 1)
                log.info("Ollama responded in %ss (%d chars, attempt %d)", elapsed, len(raw), attempt + 1)
                return raw

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            wait = 3 * (2 ** attempt)
            log.warning("Ollama attempt %d failed (%s). Retrying in %ds...", attempt + 1, e.__class__.__name__, wait)
            if attempt < OLLAMA_MAX_RETRIES:
                time.sleep(wait)
            else:
                log.error("All %d Ollama attempts failed. Falling back to template.", OLLAMA_MAX_RETRIES + 1)
                return None

        except httpx.HTTPStatusError as e:
            log.error("Ollama HTTP error %s: %s", e.response.status_code, e.response.text[:200])
            return None

        except Exception as e:
            log.error("Unexpected error calling Ollama: %s", e)
            return None

    return None


# ── Output Validation (Hallucination Guard) ──────────────────────────────────

def _extract_numbers(text: str) -> set:
    """Extract all numeric values from text as floats."""
    # Match integers, decimals, and percentages
    matches = re.findall(r'(?<!\w)(\d+\.?\d*)\s*%?', text)
    return {float(m) for m in matches}


def _collect_context_numbers(context: dict) -> set:
    """Recursively collect all numeric values from the context dict."""
    numbers = set()

    def _walk(obj):
        if isinstance(obj, (int, float)):
            numbers.add(float(obj))
            # Also add common transformations
            numbers.add(round(float(obj), 0))
            numbers.add(round(float(obj), 1))
            numbers.add(round(float(obj), 2))
            numbers.add(round(float(obj) * 100, 1))  # percentage form
            numbers.add(round(float(obj) * 100, 0))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _walk(v)

    _walk(context)
    return numbers


def validate_report(llm_json: dict, context: dict) -> tuple:
    """
    Validate the LLM's structured narrative output against the input context.

    Returns (is_valid, issues_list).
    """
    issues = []

    # 1. Required sections (narrative schema)
    for key in ("overview", "per_game_narrative", "recommendations"):
        if key not in llm_json:
            issues.append(f"Missing required section: '{key}'")

    # 2. Overview minimum length
    overview = llm_json.get("overview", "")
    if len(overview) < 30:
        issues.append(f"Overview too short ({len(overview)} chars)")

    # 3. Per-game entries should reference games from the context
    context_games = set(context.get("games", {}).keys())
    llm_games = {entry.get("game", "") for entry in llm_json.get("per_game_narrative", [])}
    invented_games = llm_games - context_games - {""}
    if invented_games:
        issues.append(f"Report mentions games not in input: {invented_games}")
    missing_games = context_games - llm_games
    if missing_games:
        issues.append(f"Report omitted games from input: {missing_games}")

    # 4. Narrative should not contain raw technical metrics
    technical_terms = ["d_prime", "d-prime", "commission_error", "omission_error", "delta_pct", "Δ"]
    all_text = overview + " ".join(e.get("narrative", "") for e in llm_json.get("per_game_narrative", []))
    for term in technical_terms:
        if term.lower() in all_text.lower():
            issues.append(f"Technical term '{term}' found in narrative — should use plain language")

    is_valid = len(issues) == 0
    if issues:
        log.warning("Report validation issues: %s", "; ".join(issues))
    else:
        log.info("Report validation passed.")

    return is_valid, issues


def _parse_llm_json(raw: str) -> dict | None:
    """Attempt to parse JSON from an LLM response, stripping markdown fences."""
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    log.warning("Failed to parse LLM output as JSON.")
    return None


# ── Markdown Renderer (from structured JSON) ────────────────────────────────

def _render_markdown(report_data: dict, context: dict) -> str:
    """Render a validated narrative report dict into polished markdown."""
    pid = context["participant_id"]
    date = context.get("generated_at", datetime.now().isoformat())
    has_compare = context.get("has_comparison", False)
    locale = normalize_report_locale(context.get("locale"))
    report_type_text = _copy(locale, "report_type.progress") if has_compare else _copy(locale, "report_type.assessment")

    lines = [
        f"# 🧠 NeuroGames — {_copy(locale, 'title')}",
        f"",
        f"**{_copy(locale, 'child_id')}:** {pid}  ",
        f"**{_copy(locale, 'date')}:** {date[:10]}  ",
        f"**{_copy(locale, 'report_type_label')}:** {report_type_text}",
        f"",
        f"---",
        f"",
        f"## {_copy(locale, 'overview_header')}",
        f"",
        report_data.get("overview", "No overview available."),
        f"",
    ]

    # Per-game narrative
    narratives = report_data.get("per_game_narrative", [])
    if narratives:
        lines.append("---")
        lines.append("")
        lines.append(f"## {_copy(locale, 'games_header')}")
        lines.append("")
        for entry in narratives:
            game = entry.get("game", "unknown")
            display_name = _game_display_name(game, locale)
            domain = _game_domain(game, locale)
            game_ctx = context.get("games", {}).get(game, {})
            severity = game_ctx.get("severity", "normal")
            sev_emoji = {"normal": "🟢", "mild": "🟡", "moderate": "🟠", "severe": "🔴"}.get(severity, "⚪")

            lines.append(f"### {sev_emoji} {display_name}")
            if domain:
                lines.append(f"*{_copy(locale, 'measures_label')}: {domain}*")
            lines.append(f"")
            lines.append(entry.get("narrative", "Not enough data to describe performance."))
            lines.append(f"")

    # Improvement narrative
    imp_narrative = report_data.get("improvement_narrative", "")
    if imp_narrative:
        lines.append("---")
        lines.append("")
        lines.append(f"## {_copy(locale, 'progress_header')}")
        lines.append("")
        lines.append(imp_narrative)
        lines.append("")

    # Recommendations
    recs = report_data.get("recommendations", [])
    if recs:
        lines.append("---")
        lines.append("")
        lines.append(f"## {_copy(locale, 'recommendations_header')}")
        lines.append("")
        for i, rec in enumerate(recs, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    lines.append("---")
    lines.append(f"*{_copy(locale, 'auto_generated')}*")

    return "\n".join(lines)


# ── Rich Template Fallback ───────────────────────────────────────────────────

def _template_report(context: dict) -> str:
    """
    Rich fallback report when Ollama is unavailable.
    Generates parent-friendly narrative markdown from raw data using interpretation helpers.
    """
    pid = context["participant_id"]
    is_indiv = context.get("is_individual", True)
    date = context.get("generated_at", datetime.now().isoformat())
    has_compare = context.get("has_comparison", False)
    locale = normalize_report_locale(context.get("locale"))
    report_type_text = _copy(locale, "report_type.progress") if has_compare else _copy(locale, "report_type.assessment")

    lines = [
        f"# 🧠 NeuroGames — {_copy(locale, 'title')}",
        f"",
        f"**{_copy(locale, 'child_id')}:** {pid}  ",
        f"**{_copy(locale, 'date')}:** {date[:10]}  ",
        f"**{_copy(locale, 'report_type_label')}:** {report_type_text}",
        f"",
        f"---",
        f"",
    ]

    games = context.get("games", {})
    if not games:
        lines.append(f"*{_copy(locale, 'no_data')}*")
        return "\n".join(lines)

    if not is_indiv:
        # GLOBAL PIPELINE REPORT (admin-facing, keep technical)
        lines.append("## Pipeline Module Performance")
        lines.append("")
        for game, stats in games.items():
            display_name = _game_display_name(game, locale)
            lines.append(f"### 🎮 {display_name} ({stats.get('cognitive_domain', 'Unknown')})")
            lines.append("")
            for mod, mdata in stats.get("modules", {}).items():
                friendly = MODULE_FRIENDLY.get(mod, mod)
                acc = mdata.get("accuracy")
                level_label, emoji, explanation = _accuracy_to_level(acc)
                lines.append(f"- **{friendly}:** {emoji} {level_label}")
                if acc is not None:
                    lines.append(f"  - {explanation}")
            lines.append("")
        lines.append("---")
        lines.append("*Auto-generated pipeline report.*")
        return "\n".join(lines)

    # ── INDIVIDUAL PARTICIPANT REPORT (narrative style) ───────────────────
    total_sessions = sum(g.get("total_sessions", 0) for g in games.values())
    clusters = [g.get("clinical_cluster", "Unknown") for g in games.values() if g.get("clinical_cluster", "Unknown") != "Unknown"]
    primary_cluster = max(set(clusters), key=clusters.count) if clusters else "Unknown"
    severities = [g.get("severity", "normal") for g in games.values()]
    worst_severity = "severe" if "severe" in severities else \
                     "moderate" if "moderate" in severities else \
                     "mild" if "mild" in severities else "normal"

    # Build overall narrative summary
    lines.append(f"## {_copy(locale, 'overview_header')}")
    lines.append("")

    # Strengths and challenges
    strengths = [_game_display_name(g, locale) for g, s in games.items() if s.get("severity") == "normal"]
    challenges = [_game_display_name(g, locale) for g, s in games.items() if s.get("severity") in ("moderate", "severe")]

    overview_parts = [_copy(locale, "overview_total", sessions=total_sessions, games=len(games))]

    if strengths:
        overview_parts.append(_copy(locale, "overview_strengths", items=", ".join(strengths)))
    if challenges:
        overview_parts.append(_copy(locale, "overview_challenges", items=", ".join(challenges)))
    elif not strengths:
        overview_parts.append(_copy(locale, "overview_developing"))

    # Cluster narrative
    cluster_narrative = _interpret_cluster(primary_cluster, locale)
    if cluster_narrative:
        overview_parts.append(cluster_narrative)

    # Overall severity
    sev_narrative = _interpret_severity(worst_severity, locale)
    if sev_narrative and worst_severity != "normal":
        overview_parts.append(sev_narrative)

    lines.append(" ".join(overview_parts))
    lines.append("")

    # Per-game narrative sections
    lines.append("---")
    lines.append("")
    lines.append(f"## {_copy(locale, 'games_header')}")
    lines.append("")

    sev_emoji = {"normal": "🟢", "mild": "🟡", "moderate": "🟠", "severe": "🔴"}

    for game, stats in games.items():
        display_name = _game_display_name(game, locale)
        domain = stats.get("cognitive_domain", "")
        severity = stats.get("severity", "normal")
        sev_icon = sev_emoji.get(severity, "⚪")

        lines.append(f"### {sev_icon} {display_name}")
        if domain:
            lines.append(f"*{_copy(locale, 'measures_label')}: {domain}*")
        lines.append("")

        # Use the pre-built narrative from build_context
        narrative = stats.get("narrative", "")
        if narrative:
            lines.append(narrative)
        else:
            lines.append(_copy(locale, "no_detail_game", game=display_name))
        lines.append("")

        # Improvement narrative (if available)
        imp_narrative = stats.get("improvement_narrative", "")
        if imp_narrative:
            lines.append(f"**{_copy(locale, 'progress_label')}:** {imp_narrative}")
            lines.append("")

    # Recommendations (parent-friendly)
    lines.append("---")
    lines.append("")
    lines.append(f"## {_copy(locale, 'recommendations_header')}")
    lines.append("")

    rec_num = 1
    if worst_severity in ("moderate", "severe"):
        lines.append(f"{rec_num}. {_copy(locale, 'rec.specialist')}")
        rec_num += 1
    if any(g.get("avg_frustration_clicks", 0) >= 5 for g in games.values()):
        lines.append(f"{rec_num}. {_copy(locale, 'rec.breaks')}")
        rec_num += 1
    if any("Inattentive" in g.get("clinical_cluster", "") for g in games.values()):
        lines.append(f"{rec_num}. {_copy(locale, 'rec.focus')}")
        rec_num += 1
    if has_compare:
        any_regress = any(
            g.get("improvement_analysis", {}).get("correct", {}).get("delta", 0) < 0
            for g in games.values()
        )
        if any_regress:
            lines.append(f"{rec_num}. {_copy(locale, 'rec.regress')}")
        else:
            lines.append(f"{rec_num}. {_copy(locale, 'rec.progress')}")
        rec_num += 1
    lines.append(f"{rec_num}. {_copy(locale, 'rec.check_back')}")
    lines.append("")

    lines.append("---")
    lines.append(f"*{_copy(locale, 'auto_generated')}*")

    return "\n".join(lines)


# ── Structured Payload Builder ───────────────────────────────────────────────

def _ordered_context_games(context: dict) -> list[str]:
    """Return context games in product order, preserving any unknown extras."""
    games = context.get("games", {})
    ordered = [game for game in _registered_report_game_ids() if game in games]
    ordered.extend(game for game in games.keys() if game not in ordered)
    return ordered


def _json_number(value):
    """Return a JSON-friendly number, or None for missing/NaN values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 3)


def _primary_cluster(games: dict) -> str:
    clusters = [
        stats.get("clinical_cluster", "Unknown")
        for stats in games.values()
        if stats.get("clinical_cluster", "Unknown") != "Unknown"
    ]
    return max(set(clusters), key=clusters.count) if clusters else "Unknown"


def _worst_severity(games: dict) -> str:
    ranking = {"normal": 0, "mild": 1, "moderate": 2, "severe": 3}
    severities = [stats.get("severity", "normal") for stats in games.values()]
    if not severities:
        return "normal"
    return max(severities, key=lambda severity: ranking.get(severity, 0))


def _fallback_overview(context: dict) -> str:
    """Build a concise overall summary from context when no LLM overview exists."""
    locale = normalize_report_locale(context.get("locale"))
    games = context.get("games", {})
    if not games:
        return _copy(locale, "no_data")

    total_sessions = sum(stats.get("total_sessions", 0) for stats in games.values())
    strengths = [
        _game_display_name(game, locale)
        for game, stats in games.items()
        if stats.get("severity") == "normal"
    ]
    challenges = [
        _game_display_name(game, locale)
        for game, stats in games.items()
        if stats.get("severity") in ("moderate", "severe")
    ]

    parts = [
        _copy(locale, "overview_total", sessions=total_sessions, games=len(games))
    ]
    if strengths:
        parts.append(_copy(locale, "overview_strengths", items=", ".join(strengths)))
    if challenges:
        parts.append(_copy(locale, "overview_challenges", items=", ".join(challenges)))
    elif not strengths:
        parts.append(_copy(locale, "overview_developing"))

    cluster_narrative = _interpret_cluster(_primary_cluster(games), locale)
    if cluster_narrative:
        parts.append(cluster_narrative)
    return " ".join(parts)


def _build_structured_report(context: dict, narrative_data: dict | None = None) -> dict:
    """
    Build the web-facing structured report payload.

    Every game present in the source context is carried through, even when an
    LLM response omits a per-game section.
    """
    games = context.get("games", {})
    locale = normalize_report_locale(context.get("locale"))
    narrative_data = narrative_data or {}
    llm_narratives = {
        entry.get("game"): entry.get("narrative")
        for entry in narrative_data.get("per_game_narrative", [])
        if entry.get("game")
    }

    structured_games = {}
    for game in _ordered_context_games(context):
        stats = games[game]
        current = stats.get("current", {})
        structured_games[game] = {
            "cognitive_domain": stats.get("cognitive_domain", _game_domain(game, locale)),
            "total_sessions": int(stats.get("total_sessions", 0) or 0),
            "severity": stats.get("severity", "normal"),
            "clinical_cluster": _cluster_label(stats.get("clinical_cluster", "Unknown"), locale),
            "narrative": llm_narratives.get(game) or stats.get("narrative", ""),
            "improvement_narrative": stats.get("improvement_narrative", ""),
            "cluster_narrative": stats.get("cluster_narrative", ""),
            "comparison_window": stats.get("comparison_window"),
            "highest_level_reached": _json_number(stats.get("highest_level_reached")),
            "accuracy_rate": _json_number(stats.get("accuracy_rate", current.get("accuracy_rate"))),
            "avg_reaction_time": _json_number(stats.get("avg_rt", current.get("avg_rt"))),
            "avg_frustration_clicks": _json_number(
                stats.get("avg_frustration_clicks", current.get("avg_frustration_clicks"))
            ),
        }

    total_sessions = sum(game["total_sessions"] for game in structured_games.values())
    return {
        "participant_id": context.get("participant_id", ""),
        "generated_at": context.get("generated_at", datetime.now().isoformat()),
        "locale": locale,
        "snapshot_until": context.get("snapshot_until"),
        "session_count_at_snapshot": context.get("session_count_at_snapshot", total_sessions),
        "report_type": _copy(locale, "report_type.progress" if context.get("has_comparison") else "report_type.assessment"),
        "games": structured_games,
        "overall": {
            "total_sessions": total_sessions,
            "games_played": len(structured_games),
            "primary_cluster": _cluster_label(_primary_cluster(games), locale),
            "worst_severity": _worst_severity(games),
            "narrative_summary": narrative_data.get("overview") or _fallback_overview(context),
        },
        "recommendations": narrative_data.get("recommendations", []),
    }


def _structured_sidecar_path(report_path: str) -> str:
    return os.path.splitext(report_path)[0] + ".json"


def _load_report_sidecar(report_path: str) -> dict | None:
    sidecar_path = _structured_sidecar_path(report_path)
    if not os.path.exists(sidecar_path):
        return None
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_structured_sidecar(report_path: str) -> dict | None:
    payload = _load_report_sidecar(report_path)
    if not payload:
        return None
    if "structured" in payload:
        return payload.get("structured")
    return payload


# ── Report Caching ───────────────────────────────────────────────────────────

def _context_hash(context: dict) -> str:
    """SHA-256 hash of the context dict for deduplication."""
    # Exclude generated_at since it changes every call
    ctx_copy = {k: v for k, v in context.items() if k != "generated_at"}
    raw = json.dumps(ctx_copy, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _find_cached_report(
    participant_id: str,
    ctx_hash: str,
    suffix: str,
    locale: str = None,
    report_group_id: str = None,
) -> str | None:
    """Check if a report with the same context hash already exists."""
    locale = normalize_report_locale(locale)
    if not os.path.isdir(REPORTS_DIR):
        return None
    for fname in os.listdir(REPORTS_DIR):
        if not fname.endswith(".md"):
            continue
        if report_group_id:
            if fname != f"{report_group_id}_{locale}.md":
                continue
        elif not fname.startswith(f"{participant_id}_{suffix}_"):
            continue

        path = os.path.join(REPORTS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if f"ctx:{ctx_hash}" in content:
                log.info("Cache hit: %s", fname)
                return path
        except OSError:
            continue
    return None


def _report_groups_for_participant(participant_id: str) -> dict:
    """Return report groups keyed by locale-neutral report_group_id."""
    groups = {}
    if not os.path.isdir(REPORTS_DIR):
        return groups

    import re
    pattern = re.compile(
        rf"^{re.escape(participant_id)}_(snapshot|progress)_(\d{{8}})_(\d{{4}})(?:_([a-z]{{2}}))?\.md$"
    )

    for fname in os.listdir(REPORTS_DIR):
        m = pattern.match(fname)
        if not m:
            continue

        report_type, date_str, time_str, locale = m.groups()
        locale = normalize_report_locale(locale)
        report_group_id = f"{participant_id}_{report_type}_{date_str}_{time_str}"
        timestamp = f"{date_str}_{time_str}"
        generated_at = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}T{time_str[:2]}:{time_str[2:]}:59"
        path = os.path.join(REPORTS_DIR, fname)
        sidecar = _load_report_sidecar(path) or {}
        metadata = sidecar.get("metadata", {}) if isinstance(sidecar.get("metadata"), dict) else {}

        group = groups.setdefault(report_group_id, {
            "report_group_id": report_group_id,
            "report_type": report_type,
            "timestamp": timestamp,
            "generated_at": metadata.get("generated_at") or generated_at,
            "snapshot_until": metadata.get("snapshot_until") or generated_at,
            "session_count_at_snapshot": metadata.get("session_count_at_snapshot"),
            "variants": {},
        })
        group["variants"][locale] = path

        # Prefer explicit sidecar metadata when any variant has it.
        if metadata.get("snapshot_until"):
            group["snapshot_until"] = metadata["snapshot_until"]
        if metadata.get("generated_at"):
            group["generated_at"] = metadata["generated_at"]
        if metadata.get("session_count_at_snapshot") is not None:
            group["session_count_at_snapshot"] = metadata["session_count_at_snapshot"]

    return groups


def find_latest_report_group(participant_id: str) -> dict | None:
    """Return the newest locale-neutral report group for a participant."""
    groups = _report_groups_for_participant(participant_id)
    if not groups:
        return None
    return max(groups.values(), key=lambda group: group["timestamp"])


def _read_report_file(path: str, metadata: dict) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            report_text = f.read()
    except OSError:
        return None

    sidecar = _load_report_sidecar(path) or {}
    structured = sidecar.get("structured") if "structured" in sidecar else None
    method = sidecar.get("method") or "pre_generated"
    validation = sidecar.get("validation") or {"valid": True, "issues": []}

    return {
        **metadata,
        "path": path,
        "filename": os.path.basename(path),
        "report_text": report_text,
        "method": method,
        "structured": structured,
        "validation": validation,
    }


def find_latest_report(participant_id: str, locale: str = None) -> dict | None:
    """
    Scan REPORTS_DIR for the most recent pre-generated report for a participant.
    Returns {path, filename, generated_at, report_type} or None.
    """
    locale = normalize_report_locale(locale)
    group = find_latest_report_group(participant_id)
    if not group:
        return None

    path = (
        group["variants"].get(locale)
        or group["variants"].get(DEFAULT_REPORT_LOCALE)
        or next(iter(group["variants"].values()))
    )
    selected_locale = next(
        (code for code, variant_path in group["variants"].items() if variant_path == path),
        DEFAULT_REPORT_LOCALE,
    )
    report = _read_report_file(path, {
        "report_group_id": group["report_group_id"],
        "report_type": group["report_type"],
        "timestamp": group["timestamp"],
        "generated_at": group["generated_at"],
        "snapshot_until": group["snapshot_until"],
        "session_count_at_snapshot": group.get("session_count_at_snapshot"),
        "locale": selected_locale,
        "requested_locale": locale,
    })
    if report and report["structured"] is None and participant_id != "pipeline_run":
        try:
            context = build_context(
                participant_id,
                compare=report.get("report_type") == "progress",
                locale=selected_locale,
                snapshot_until=report.get("snapshot_until"),
                generated_at=report.get("generated_at"),
            )
            if context.get("games"):
                report["structured"] = _build_structured_report(context)
        except Exception as e:
            log.debug("Could not rebuild structured report for %s: %s", participant_id, e)
    return report


def get_or_create_latest_report_variant(participant_id: str, locale: str = None) -> dict | None:
    """
    Return the latest report in the requested locale.

    If the latest report group exists only in another language, generate a new
    locale variant from that group's stored snapshot cutoff. This intentionally
    does not include sessions played after the original report snapshot.
    """
    locale = normalize_report_locale(locale)
    group = find_latest_report_group(participant_id)
    if not group:
        return None

    if locale in group["variants"]:
        sidecar = _load_report_sidecar(group["variants"][locale]) or {}
        metadata = sidecar.get("metadata", {}) if isinstance(sidecar.get("metadata"), dict) else {}
        validation = sidecar.get("validation") if isinstance(sidecar.get("validation"), dict) else {}
        failed_llm_fallback = sidecar.get("method") == "template" and validation.get("valid") is False
        if metadata.get("i18n_version") == REPORT_I18N_VERSION and not failed_llm_fallback:
            return find_latest_report(participant_id, locale=locale)

    return generate_report(
        participant_id,
        compare=group["report_type"] == "progress",
        locale=locale,
        snapshot_until=group["snapshot_until"],
        report_group_id=group["report_group_id"],
        generated_at=group["generated_at"],
        is_canonical=False,
        force_regenerate=True,
    )


# ── Main Generator ───────────────────────────────────────────────────────────

def generate_report(
    participant_id: str,
    pipeline_summary: list = None,
    compare: bool = False,
    locale: str = None,
    snapshot_until: str = None,
    report_group_id: str = None,
    generated_at: str = None,
    is_canonical: bool = True,
    force_regenerate: bool = False,
) -> dict:
    """
    Generate a clinical report for a participant or pipeline run.

    Args:
        participant_id: "pipeline_run" for global, or a specific participant ID.
        pipeline_summary: Raw results from the pipeline runner (global mode only).
        compare: If True, includes improvement/trend analysis.
        locale: Human-readable report language (fr, en, ar).
        snapshot_until: Optional fixed session cutoff for language variants.
        report_group_id: Locale-neutral report identity shared by variants.

    Returns:
        {
            "participant_id": str,
            "report_text": str,
            "report_path": str,
            "method": "llm" | "template",
            "validation": {"valid": bool, "issues": list},
        }
    """
    t0 = time.time()
    pipeline_summary = pipeline_summary or []
    locale = normalize_report_locale(locale)
    suffix = "progress" if compare else "snapshot"
    generated_at = generated_at or datetime.now().isoformat()
    snapshot_until = snapshot_until or generated_at
    if not report_group_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        report_group_id = f"{participant_id}_{suffix}_{timestamp}"
        counts_as_new_report = bool(is_canonical)
    else:
        counts_as_new_report = False

    context = build_context(
        participant_id,
        pipeline_summary,
        compare=compare,
        locale=locale,
        snapshot_until=snapshot_until,
        generated_at=generated_at,
    )

    ctx_hash = _context_hash(context)

    # Check cache
    cached = None if force_regenerate else _find_cached_report(
        participant_id,
        ctx_hash,
        suffix,
        locale=locale,
        report_group_id=report_group_id,
    )
    if cached:
        with open(cached, "r", encoding="utf-8") as f:
            cached_text = f.read()
        structured = _load_structured_sidecar(cached) or _build_structured_report(context)
        metadata = {
            "report_group_id": report_group_id,
            "locale": locale,
            "snapshot_until": snapshot_until,
            "generated_at": generated_at,
            "session_count_at_snapshot": context.get("session_count_at_snapshot", 0),
            "is_canonical": bool(is_canonical),
            "i18n_version": REPORT_I18N_VERSION,
        }
        return {
            "participant_id": participant_id,
            "report_text": cached_text,
            "report_path": cached,
            "method": "cached",
            "structured": structured,
            **metadata,
            "counts_as_new_report": False,
            "validation": {"valid": True, "issues": []},
        }

    # Try LLM generation
    method = "template"
    validation_result = {"valid": True, "issues": []}
    narrative_data = None

    if context.get("is_individual", True) and context.get("games"):
        system_prompt, user_prompt = _build_prompt(context)
        raw_llm = call_llm(system_prompt, user_prompt)

        if raw_llm:
            llm_json = _parse_llm_json(raw_llm)
            if llm_json:
                is_valid, issues = validate_report(llm_json, context)
                validation_result = {"valid": is_valid, "issues": issues}

                if is_valid:
                    report_text = _render_markdown(llm_json, context)
                    narrative_data = llm_json
                    method = "llm"
                else:
                    log.warning("LLM output failed validation. Falling back to template.")
                    report_text = _template_report(context)
            else:
                log.warning("LLM output was not valid JSON. Falling back to template.")
                validation_result = {"valid": False, "issues": ["LLM output not parseable as JSON"]}
                report_text = _template_report(context)
        else:
            if LLM_PROVIDER not in {"template", "none", "off", "disabled"}:
                validation_result = {"valid": False, "issues": ["LLM provider unavailable"]}
            report_text = _template_report(context)
    else:
        report_text = _template_report(context)

    structured = _build_structured_report(context, narrative_data)

    # Append cache hash as footer comment
    report_text += f"\n\n<!-- ctx:{ctx_hash} -->\n"

    # Save to disk
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f"{report_group_id}_{locale}.md"
    report_path = os.path.join(REPORTS_DIR, filename)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    structured_path = _structured_sidecar_path(report_path)
    metadata = {
        "report_group_id": report_group_id,
        "locale": locale,
        "snapshot_until": snapshot_until,
        "generated_at": generated_at,
        "session_count_at_snapshot": context.get("session_count_at_snapshot", 0),
        "is_canonical": bool(is_canonical),
        "context_hash": ctx_hash,
        "i18n_version": REPORT_I18N_VERSION,
    }
    with open(structured_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "structured": structured,
                "method": method,
                "validation": validation_result,
                "metadata": metadata,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    elapsed = round(time.time() - t0, 1)
    log.info(
        "Report generated: %s | method=%s | valid=%s | %.1fs",
        filename, method, validation_result["valid"], elapsed
    )

    return {
        "participant_id": participant_id,
        "report_text": report_text,
        "report_path": report_path,
        "method": method,
        "structured": structured,
        "structured_path": structured_path,
        **metadata,
        "counts_as_new_report": counts_as_new_report,
        "validation": validation_result,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Clinical Report (v2)")
    parser.add_argument("--participant", default="P001", help="Participant ID")
    parser.add_argument("--compare", action="store_true", help="Include improvement analysis")
    parser.add_argument("--all", action="store_true", help="Process all participants")
    parser.add_argument("--locale", default=DEFAULT_REPORT_LOCALE, help="Report language: fr, en, or ar")
    parser.add_argument("--provider", default=None, help="LLM provider: gemini, ollama, or template")
    parser.add_argument("--model", default=None, help="LLM model override for the selected provider")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    # Fix Windows console encoding for emoji output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.provider:
        LLM_PROVIDER = args.provider.strip().lower()

    if args.model:
        if LLM_PROVIDER in {"gemini", "google", "google-gemini"}:
            GEMINI_MODEL = args.model
        elif LLM_PROVIDER == "ollama":
            OLLAMA_MODEL = args.model
        else:
            GEMINI_MODEL = args.model

    if args.all:
        pids = list_participant_ids()
        log.info("Batch: generating reports for %d participants (compare=%s)", len(pids), args.compare)
        for pid in pids:
            result = generate_report(pid, compare=args.compare, locale=args.locale)
            status = "OK" if result["validation"]["valid"] else "WARN"
            print(f"  [{status}] {pid}: {result['method']} -> {os.path.basename(result['report_path'])}")
    else:
        result = generate_report(args.participant, compare=args.compare, locale=args.locale)
        print(f"\nMethod: {result['method']}")
        print(f"Valid: {result['validation']['valid']}")
        if result['validation']['issues']:
            print(f"Issues: {result['validation']['issues']}")
        print(f"Path: {result['report_path']}\n")
        print(result["report_text"])

