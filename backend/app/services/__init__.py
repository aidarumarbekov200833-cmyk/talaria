"""Compatibility facade for the staged ``app.services`` extraction."""

from . import _legacy
from ._legacy import (
    DEFAULT_SYSTEM_PROMPT,
    SCORE_SUBS_CEIL,
    SCORE_SUBS_FLOOR,
    SCORE_VPV_CEIL,
    SCORE_VPV_FLOOR,
    SCORE_WEIGHT_ACTIVITY,
    SCORE_WEIGHT_AUDIENCE,
    SCORE_WEIGHT_ENGAGEMENT,
    SCORE_WEIGHT_RELEVANCE,
    WARMUP_DISCLAIMER,
    ai_provider_ready,
    build_system_prompt,
    compute_lead_score,
    explain_lead_score,
    resolve_ai_provider,
)
from .events import record_event as log_event
from .settings import current_period, get_settings, roll_month

_log_scale = _legacy._log_scale

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "build_system_prompt",
    "WARMUP_DISCLAIMER",
    "get_settings",
    "resolve_ai_provider",
    "ai_provider_ready",
    "current_period",
    "roll_month",
    "log_event",
    "SCORE_WEIGHT_AUDIENCE",
    "SCORE_WEIGHT_ENGAGEMENT",
    "SCORE_WEIGHT_ACTIVITY",
    "SCORE_WEIGHT_RELEVANCE",
    "SCORE_SUBS_FLOOR",
    "SCORE_SUBS_CEIL",
    "SCORE_VPV_FLOOR",
    "SCORE_VPV_CEIL",
    "compute_lead_score",
    "explain_lead_score",
]
