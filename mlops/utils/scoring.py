"""
Conners' Global Index (CGI) Scoring Utility.

The CGI-10 (Conners' Global Index, 10-item) uses a 4-point Likert scale (0-3).
Total Score Range: 0 - 30.

Clinical Thresholds (approximate):
- > 15: Suggests potential ADHD active symptoms (Parent rating).
- > 12: Borderline (Parent rating).
"""

def calculate_cgi_subscales(answers: dict[int, int]) -> dict[str, int]:
    """
    Divide the 10 items into the standard CGI-10 subscales:
    - Restless-Impulsive (RI): Items 1-7
    - Emotional Lability (EL): Items 8-10
    """
    ri_items = [1, 2, 3, 4, 5, 6, 7]
    el_items = [8, 9, 10]
    
    # Answers coming from frontend are 1-indexed keys
    ri_score = sum(answers.get(i, 0) for i in ri_items)
    el_score = sum(answers.get(i, 0) for i in el_items)
    total = ri_score + el_score
    
    return {
        "restless_impulsive": ri_score,
        "emotional_lability": el_score,
        "total": total
    }

def calculate_cgi_score(answers: dict[int, int]) -> int:
    """Sum the 10 items of the CGI-10 assessment."""
    return sum(answers.values())

def get_clinical_interpretation(score: int) -> str:
    """Return a brief interpretation based on the total score and T-score logic."""
    t_score = normalize_to_tscore(score)
    if t_score >= 70:
        return "Significantly Elevated (Strong indicators of ADHD-related patterns)"
    if t_score >= 65:
        return "Elevated (Clinical attention strongly recommended)"
    if t_score >= 60:
        return "Borderline / Raised (Monitoring and further assessment suggested)"
    return "Typical / Within Normal Range"

def normalize_to_tscore(raw_score: int, age: int | None = None) -> float:
    """
    Standardize the raw score to a T-Score (Mean=50, SD=10).
    Using approximate population norms for children (6-14 years):
    General Total CGI Mean ≈ 5.0, SD ≈ 4.5
    T = ((Raw - Mean) / SD) * 10 + 50
    """
    population_mean = 5.0
    population_sd = 4.5
    
    # Ensure raw_score is clamped to valid range
    clamped = max(0, min(30, raw_score))
    
    t_score = ((clamped - population_mean) / population_sd) * 10 + 50
    return round(t_score, 1)


# ── Tier Classification ──────────────────────────────────────────────────────

CONNERS_TIERS = ("typical", "borderline", "elevated", "significant")

def get_conners_tier(raw_score: int) -> str:
    """
    Map a raw CGI-10 score to a severity tier using T-score thresholds.

    Returns one of: 'typical', 'borderline', 'elevated', 'significant'.
    """
    t = normalize_to_tscore(raw_score)
    if t >= 70:
        return "significant"
    if t >= 65:
        return "elevated"
    if t >= 60:
        return "borderline"
    return "typical"


def tier_distance(tier_a: str, tier_b: str) -> int:
    """Return the ordinal distance between two severity tiers (0-3)."""
    order = {t: i for i, t in enumerate(CONNERS_TIERS)}
    return abs(order.get(tier_a, 0) - order.get(tier_b, 0))
