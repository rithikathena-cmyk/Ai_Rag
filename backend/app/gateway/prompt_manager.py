from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

# backend/app/gateway/prompt_manager.py -> parents[2] == backend/
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptNotFoundError(Exception):
    pass


@dataclass
class PromptTemplate:
    name: str
    version: str
    text: str
    changelog: list[str]


@lru_cache(maxsize=None)
def load_prompt(name: str, version: str = "v1") -> PromptTemplate:
    """Loads backend/prompts/{name}_{version}.yaml. Cached — prompt files are
    static per process lifetime; bump the version (new file) rather than
    editing one in place, so a running deployment's behavior never changes
    out from under it and old requests remain reproducible."""
    path = _PROMPTS_DIR / f"{name}_{version}.yaml"
    if not path.exists():
        raise PromptNotFoundError(f"No prompt file at {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return PromptTemplate(
        name=data["name"], version=data["version"], text=data["text"], changelog=data.get("changelog", [])
    )
