from app.gateway import model_router
from app.gateway.schemas import ModelTier


def test_resolve_fast_tier_returns_config():
    cfg = model_router.resolve(ModelTier.FAST)
    assert cfg.model
    assert cfg.max_tokens > 0
    assert cfg.effort


def test_resolve_reasoning_tier_returns_config():
    cfg = model_router.resolve(ModelTier.REASONING)
    assert cfg.model


def test_resolve_haiku_tier_returns_a_distinct_model():
    # Role-based model-access policy — haiku must route to its own model,
    # not silently alias sonnet/opus.
    haiku = model_router.resolve(ModelTier.HAIKU)
    sonnet = model_router.resolve(ModelTier.SONNET)
    opus = model_router.resolve(ModelTier.OPUS)
    assert haiku.model
    assert haiku.model != sonnet.model
    assert haiku.model != opus.model


def test_resolve_defaults_to_default_tier_when_none_given():
    assert model_router.resolve(None) == model_router.resolve(model_router.default_tier())


def test_resolve_falls_back_to_fast_for_a_tier_missing_from_config(monkeypatch):
    monkeypatch.setattr(
        model_router,
        "_config",
        lambda: {"tiers": {"fast": {"model": "m", "max_tokens": 10, "effort": "low"}}, "default_tier": "fast"},
    )
    cfg = model_router.resolve(ModelTier.REASONING)  # not present in this config
    assert cfg.model == "m"
