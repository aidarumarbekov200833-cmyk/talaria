"""Compatibility contract for the temporary ``app.services`` facade."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

PUBLIC_NAMES = [
    "DEFAULT_SYSTEM_PROMPT", "build_system_prompt", "WARMUP_DISCLAIMER", "get_settings",
    "resolve_ai_provider", "ai_provider_ready", "current_period", "roll_month", "log_event",
    "SCORE_WEIGHT_AUDIENCE", "SCORE_WEIGHT_ENGAGEMENT", "SCORE_WEIGHT_ACTIVITY",
    "SCORE_WEIGHT_RELEVANCE", "SCORE_SUBS_FLOOR", "SCORE_SUBS_CEIL", "SCORE_VPV_FLOOR",
    "SCORE_VPV_CEIL", "compute_lead_score", "explain_lead_score",
]


def test_services_is_package_with_exact_legacy_facade() -> None:
    from app import services

    assert hasattr(services, "__path__")
    assert services.__all__ == PUBLIC_NAMES
    assert hasattr(services, "_log_scale")
    assert "_log_scale" not in services.__all__
    assert str(inspect.signature(services.get_settings)) == (
        "(db: 'Session', owner_id: 'int') -> 'AppSettings'"
    )
    assert str(inspect.signature(services.compute_lead_score)) == (
        "(subscribers: 'int | None', video_count: 'int | None', "
        "view_count: 'int | None', matched_keyword: 'str | None') -> 'int'"
    )
    assert services.compute_lead_score(100_000, 50, 500_000, "keyword") == 79
    assert services.compute_lead_score(None, 0, None, None) == 15
    assert services.explain_lead_score(1_000, 10, 10_000, "keyword")[0].startswith(
        "Небольшая аудитория:"
    )


def test_services_cold_imports_cover_all_current_consumers_and_lead_migration() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    script = """
import importlib
import importlib.util
from pathlib import Path

modules = [
    'app.services', 'app.ai_tools', 'app.ai_prompt', 'app.models', 'app.plans',
    'app.sending', 'app.routers.admin', 'app.routers.ai', 'app.routers.campaigns',
    'app.routers.leads', 'app.routers.offers', 'app.routers.search',
    'app.routers.settings', 'app.routers.warmup', 'scripts.seed',
]
for module_name in modules:
    importlib.import_module(module_name)

from app.models import Lead
assert Lead.score_reasons.fget is not None
lead = Lead(score=0)
assert lead.score_reasons == importlib.import_module('app.services').explain_lead_score(
    lead.subscribers, lead.video_count, lead.view_count, lead.matched_keyword
)

migration_path = Path('alembic/versions/20260803_0005_lead_score.py')
spec = importlib.util.spec_from_file_location('lead_score_migration', migration_path)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
assert migration.compute_lead_score(100_000, 50, 500_000, 'keyword') == 79
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)}, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr

