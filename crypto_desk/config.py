from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping, MutableMapping, Tuple


DEFAULT_SOURCES = ("panews", "binance", "odaily", "ctcn", "techflow", "catcher")
KNOWN_SOURCES = frozenset(DEFAULT_SOURCES + ("jinse", "blockbeats"))
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ConfigError(Exception):
    """Raised when the worker configuration is not safe to use."""


@dataclass(frozen=True)
class WorkerConfig:
    gemini_api_key: str
    bark_push_key: str
    primary_model: str
    review_model: str
    poll_seconds: int
    state_file: Path
    sources: Tuple[str, ...]

    @property
    def is_configured(self) -> bool:
        return bool(self.gemini_api_key and self.bark_push_key)


def load_env_file(path: Path, environ: MutableMapping[str, str]) -> None:
    """Load safe NAME=value entries without replacing explicit environment values."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        environ.setdefault(name, value)


def _poll_seconds(value: object) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 30
    return max(10, min(600, seconds))


def _sources(value: object) -> Tuple[str, ...]:
    if value is None or not str(value).strip():
        return DEFAULT_SOURCES
    sources = tuple(source.strip() for source in str(value).split(",") if source.strip())
    if not sources or any(source not in KNOWN_SOURCES for source in sources):
        raise ConfigError("unknown worker source")
    return sources


def load_config(
    environ: MutableMapping[str, str], env_file: Path, base_dir: Path
) -> WorkerConfig:
    """Load worker configuration from an explicit environment mapping and env file."""
    load_env_file(env_file, environ)
    state_value = environ.get("CID_WORKER_STATE_FILE", "")
    state_file = Path(state_value) if state_value else Path(base_dir) / ".runtime" / "worker-state.json"
    return WorkerConfig(
        gemini_api_key=environ.get("GEMINI_API_KEY", ""),
        bark_push_key=environ.get("BARK_PUSH_KEY", ""),
        primary_model=environ.get("GEMINI_PRIMARY_MODEL", "gemini-3.1-flash-lite"),
        review_model=environ.get("GEMINI_REVIEW_MODEL", "gemini-3.5-flash-lite"),
        poll_seconds=_poll_seconds(environ.get("CID_WORKER_POLL_SECONDS", "30")),
        state_file=state_file,
        sources=_sources(environ.get("CID_WORKER_SOURCES")),
    )
