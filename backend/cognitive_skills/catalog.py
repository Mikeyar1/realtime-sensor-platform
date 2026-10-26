"""Candidate construct links, kept separate from observed metric formulas.

These links describe why a task metric is shown under a skill. They are not
norms, validated composite scores, or diagnostic classifications.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    id: str
    label: str
    description: str


SKILLS = {
    "vigilance": Skill("vigilance", "Vigilance", "Sustained detection and response across trials."),
    "sustained_attention": Skill("sustained_attention", "Sustained attention", "Maintaining task-directed responding over time."),
    "reaction_latency": Skill("reaction_latency", "Reaction latency", "Time from stimulus to attributable response."),
    "response_inhibition": Skill("response_inhibition", "Response inhibition", "Withholding responses when engagement is prohibited."),
    "behavioral_discrimination": Skill("behavioral_discrimination", "Behavioral discrimination", "Selecting threat versus nonthreat responses."),
    "motor_acquisition": Skill("motor_acquisition", "Motor acquisition", "Speed of locating and engaging a spatial target."),
    "shooting_performance": Skill("shooting_performance", "Shooting performance", "Observed engagement and hit outcomes."),
}


# Associations are hypothesis/assessment context, never a derived skill score.
METRIC_SKILL_LINKS = {
    "pvt.rt_median_ms": ("reaction_latency", "vigilance", "sustained_attention", "motor_acquisition"),
    "pvt.rt_mean_ms": ("reaction_latency", "vigilance", "sustained_attention"),
    "pvt.omission_rate_pct": ("vigilance", "sustained_attention"),
    "pvt.response_rate_pct": ("vigilance", "sustained_attention"),
    "pvt.rt_slope_ms_per_trial": ("vigilance", "sustained_attention"),
    "pvt.accuracy_pct": ("shooting_performance",),
    "gng.hit_rate_pct": ("shooting_performance", "response_inhibition"),
    "gng.false_alarm_rate": ("response_inhibition",),
    "gng.correct_rejection_rate_pct": ("response_inhibition",),
    "gng.overall_accuracy_pct": ("response_inhibition",),
    "gng.rt_median_ms": ("reaction_latency",),
    "discrimination.threat_hit_rate_pct": ("behavioral_discrimination",),
    "discrimination.false_alarm_rate_pct": ("behavioral_discrimination", "response_inhibition"),
    "discrimination.correct_rejection_rate_pct": ("behavioral_discrimination", "response_inhibition"),
    "discrimination.discrimination_accuracy_pct": ("behavioral_discrimination",),
    "pvt_fitts.rt_median_ms": ("reaction_latency", "vigilance", "sustained_attention", "motor_acquisition"),
    "pvt_fitts.omission_rate_pct": ("vigilance", "sustained_attention"),
    "pvt_fitts.hit_accuracy_pct": ("shooting_performance",),
    "pvt_fitts.response_rate_pct": ("vigilance", "sustained_attention"),
}


ADAPTER_METRIC_PREFIXES = {
    "native_target_response": ("pvt.",),
    "native_threat_discrimination": ("pvt.", "discrimination."),
    "custom_go_no_go": ("gng.",),
    "custom_fitts_pvt": ("pvt_fitts.",),
}


def selected_for_skills(explicit: list[str], skills: list[str], adapter: str) -> list[str]:
    """Inherit only formulas supported by this scenario's trial adapter."""
    prefixes = ADAPTER_METRIC_PREFIXES.get(adapter, ())
    result = list(explicit)
    for metric_id, links in METRIC_SKILL_LINKS.items():
        if (metric_id not in result and metric_id.startswith(prefixes) and
                set(links).intersection(skills)):
            result.append(metric_id)
    return result
