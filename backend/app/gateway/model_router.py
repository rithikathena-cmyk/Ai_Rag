from app.core.yaml_config import load_yaml_config
from app.gateway.schemas import ModelTier, ModelTierConfig

_DEFAULTS: dict[str, dict] = {
    "tiers": {
        "fast": {"model": "claude-opus-5", "max_tokens": 4096, "effort": "medium"},
        # supports_extended_reasoning: False — verified live, claude-haiku-4-5
        # rejects both thinking={"type": "adaptive"} and output_config=
        # {"effort": ...} with a 400 invalid_request_error. This is the tier
        # every Employee-role request resolves to (llm_rbac.yaml's
        # tiers_allowed: [haiku]), so without this, every Employee chat turn
        # fails and silently degrades to the raw-search fallback — see
        # gateway/schemas.py::ModelTierConfig's own comment.
        "haiku": {"model": "claude-haiku-4-5-20251001", "max_tokens": 4096, "effort": "medium", "supports_extended_reasoning": False},
        "sonnet": {"model": "claude-sonnet-5", "max_tokens": 4096, "effort": "medium"},
        "reasoning": {"model": "claude-opus-5", "max_tokens": 4096, "effort": "high"},
        "opus": {"model": "claude-opus-5", "max_tokens": 4096, "effort": "high"},
    },
    "default_tier": "fast",
}


def _config() -> dict:
    return load_yaml_config("models.yaml") or _DEFAULTS


def default_tier() -> ModelTier:
    return ModelTier(_config().get("default_tier", _DEFAULTS["default_tier"]))


def resolve(tier: ModelTier | None = None) -> ModelTierConfig:
    """Maps a task tier to the model/params that should handle it. Falls back
    to the fast-tier default for any tier missing from config/models.yaml, so
    a partial or missing config file degrades gracefully rather than raising
    mid-request."""
    cfg = _config()
    tiers = cfg.get("tiers", _DEFAULTS["tiers"])
    resolved_tier = tier or default_tier()
    entry = tiers.get(resolved_tier.value) or tiers.get(default_tier().value) or _DEFAULTS["tiers"]["fast"]
    return ModelTierConfig(
        model=entry["model"], max_tokens=entry.get("max_tokens", 4096), effort=entry.get("effort", "medium"),
        supports_extended_reasoning=entry.get("supports_extended_reasoning", True),
    )
