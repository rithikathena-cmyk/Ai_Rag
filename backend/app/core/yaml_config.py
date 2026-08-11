"""Tiny loader for the declarative YAML config introduced alongside the
Claude Gateway/guardrails work (backend/config/*.yaml). Deliberately narrow:
it is the source of truth only for settings that are new with that work
(model-tier routing, retry/cache policy, the new retrieval/output rail
toggles). Everything that already lived in `core/config.py`'s env-driven
`Settings` keeps `Settings` as its one source of truth — this loader is never
consulted for those fields, so there's no risk of the two disagreeing.
"""

from functools import lru_cache
from pathlib import Path

import yaml

# backend/app/core/yaml_config.py -> parents[2] == backend/
_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=None)
def load_yaml_config(filename: str) -> dict:
    """Returns {} if the file is missing so every caller has a safe,
    all-defaults fallback rather than needing its own existence check."""
    path = _CONFIG_DIR / filename
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
