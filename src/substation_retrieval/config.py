"""Settings: config/settings.yaml + .env + environment overrides.

Secrets are only read from the environment or a .env file (searched upwards from the repo root,
e.g. ~/work/projects/.env). They are never logged or returned by the API.
"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent.parent
DEFAULT_SETTINGS = REPO_ROOT / "config" / "settings.yaml"

SECRET_KEYS = ("OPENAI_API_KEY", "ASTRA_API_KEY", "astra_api_key", "openai_api_key", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
               "claude_api_key", "AZURE_OPENAI_API_KEY", "SR_NEO4J_PASSWORD")

# env var -> dotted settings key
ENV_MAP = {
    "SR_DATA_DIR": "paths.data_dir",
    "SR_V1_DIR": "paths.v1_parsed_dir",
    "SR_VAR_DIR": "paths.var_dir",
    "SR_WATCH_DIR": "paths.watch_dir",
    "SR_LLM_PROVIDER": "llm.provider",
    "SR_LLM_MODEL": "llm.model",
    "ASTRA_MODEL": "llm.model",
    "SR_LLM_API_STYLE": "llm.api_style",
    "OPENAI_BASE_URL": "llm.base_url",
    "SR_RETRIEVAL_MODE": "engines.retrieval_mode",
    "CSS_KG_ENABLED": "engines.kg.enabled",
    "CSS_RAG_ENABLED": "engines.rag.enabled",
    "SR_GRAPH_BACKEND": "graph.backend",
    "SR_NEO4J_URI": "graph.neo4j_uri",
    "SR_NEO4J_USER": "graph.neo4j_user",
    "SR_HOST": "server.host",
    "SR_PORT": "server.port",
    "SR_AUTH_ENABLED": "auth.enabled",
}


def find_dotenv(start: Path = REPO_ROOT) -> Path | None:
    d = start.resolve()
    while True:
        p = d / ".env"
        if p.is_file():
            return p
        if d.parent == d:
            return None
        d = d.parent


def read_dotenv(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path:
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().replace("export ", "")
        out[k] = v.strip().strip('"').strip("'")
    return out


def _coerce(v: str) -> Any:
    lv = v.lower()
    if lv in ("true", "yes", "on", "1"):
        return True
    if lv in ("false", "no", "off", "0"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _set(d: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def get(d: dict, dotted: str, default: Any = None) -> Any:
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


class Settings:
    """Thin wrapper around the merged settings dict with path helpers."""

    def __init__(self, data: dict, env: dict[str, str], dotenv: Path | None):
        self.data = data
        self._env = env
        self.dotenv = dotenv

    def get(self, dotted: str, default: Any = None) -> Any:
        return get(self.data, dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        _set(self.data, dotted, value)

    def secret(self, *names: str) -> str | None:
        for n in names:
            if self._env.get(n):
                return self._env[n]
        return None

    def path(self, dotted: str) -> Path:
        p = Path(str(self.get(dotted)))
        if not p.is_absolute():
            p = (REPO_ROOT / p).resolve()
        return p

    @property
    def var_dir(self) -> Path:
        p = self.path("paths.var_dir")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def var(self, *parts: str, mkdir: bool = True) -> Path:
        p = self.var_dir.joinpath(*parts)
        if mkdir:
            (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
        return p

    def public(self) -> dict:
        """Settings safe to return over the API (no secrets)."""
        d = copy.deepcopy(self.data)
        d["llm"]["key_present"] = bool(self.secret(*SECRET_KEYS))
        d["llm"]["llm_key_present"] = bool(self.secret("ASTRA_API_KEY", "OPENAI_API_KEY", "astra_api_key", "openai_api_key"))
        d["dotenv_found"] = bool(self.dotenv)
        return d


def load_settings(path: str | Path | None = None, overrides: dict | None = None) -> Settings:
    path = Path(path or os.environ.get("SR_SETTINGS") or DEFAULT_SETTINGS)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    dotenv = find_dotenv()
    env = {**read_dotenv(dotenv), **os.environ}
    for ek, key in ENV_MAP.items():
        if env.get(ek) not in (None, ""):
            _set(data, key, _coerce(env[ek]))
    for k, v in (overrides or {}).items():
        _set(data, k, v)
    data.setdefault("llm", {})
    return Settings(data, env, dotenv)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
