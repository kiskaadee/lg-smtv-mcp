"""Runtime configuration loader and stdlib .env parser."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lg_smtv_mcp.errors import ErrorCode, TvError


@dataclass(frozen=True)
class TvConfig:
    """Validated runtime configuration for lg-smtv-mcp."""

    tv_ip: str
    tv_mac: str | None = None
    wol_broadcast: str = "255.255.255.255"
    client_key_path: Path = Path(".client_key.json")
    ssh_key_path: Path | None = None
    webos_ssh_passphrase: str | None = None
    ssh_known_hosts_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_hosts: tuple[str, ...] = ()
    mcp_bearer_token: str | None = None
    max_volume: int = 40
    enable_raw_ssap: bool = False


def _load_dotenv(env_path: Path) -> None:
    """Populate unset os.environ keys from a .env file using stdlib only."""
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        # Never overwrite variables already present in the process environment.
        os.environ.setdefault(key, value)


def _parse_int_range(name: str, raw: str | None, default: int, min_val: int, max_val: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw.strip())
    except ValueError as exc:
        raise TvError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be an integer between {min_val} and {max_val}, got {raw!r}",
        ) from exc
    if not (min_val <= parsed <= max_val):
        raise TvError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be between {min_val} and {max_val}, got {parsed}",
        )
    return parsed


def _optional_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _optional_path(name: str) -> Path | None:
    val = _optional_str(name)
    return Path(val) if val is not None else None


def load_config(env_path: Path | None = Path(".env")) -> TvConfig:
    """Load and validate TvConfig from environment variables with optional .env fallback."""
    if env_path is not None:
        _load_dotenv(env_path)

    tv_ip = _optional_str("TV_IP")
    if tv_ip is None:
        raise TvError(ErrorCode.INVALID_ARGUMENT, "TV_IP is required and must be non-empty")

    port = _parse_int_range("PORT", os.environ.get("PORT"), default=8765, min_val=1, max_val=65535)
    max_volume = _parse_int_range(
        "MAX_VOLUME", os.environ.get("MAX_VOLUME"), default=40, min_val=0, max_val=100
    )

    host = _optional_str("HOST") or "127.0.0.1"
    wol_broadcast = _optional_str("WOL_BROADCAST") or "255.255.255.255"
    client_key_path = _optional_path("CLIENT_KEY_PATH") or Path(".client_key.json")

    allowed_raw = os.environ.get("ALLOWED_HOSTS", "")
    allowed_hosts = tuple(part.strip() for part in allowed_raw.split(",") if part.strip())

    enable_raw_raw = (os.environ.get("ENABLE_RAW_SSAP") or "").strip().lower()
    enable_raw_ssap = enable_raw_raw in {"1", "true", "yes", "on"}

    return TvConfig(
        tv_ip=tv_ip,
        tv_mac=_optional_str("TV_MAC"),
        wol_broadcast=wol_broadcast,
        client_key_path=client_key_path,
        ssh_key_path=_optional_path("SSH_KEY_PATH"),
        webos_ssh_passphrase=os.environ.get("WEBOS_SSH_PASSPHRASE") or None,
        ssh_known_hosts_path=_optional_path("SSH_KNOWN_HOSTS_PATH"),
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        mcp_bearer_token=_optional_str("MCP_BEARER_TOKEN"),
        max_volume=max_volume,
        enable_raw_ssap=enable_raw_ssap,
    )


def require_bearer_token(config: TvConfig) -> str:
    """Return a non-empty MCP_BEARER_TOKEN or raise INVALID_ARGUMENT."""
    if config.mcp_bearer_token is None or not config.mcp_bearer_token.strip():
        raise TvError(
            ErrorCode.INVALID_ARGUMENT,
            "MCP_BEARER_TOKEN is required when running streamable-http transport",
        )
    return config.mcp_bearer_token.strip()


def require_mac(config: TvConfig) -> str:
    """Return a non-empty TV_MAC or raise INVALID_ARGUMENT."""
    if config.tv_mac is None or not config.tv_mac.strip():
        raise TvError(
            ErrorCode.INVALID_ARGUMENT,
            "TV_MAC is required for Wake-on-LAN (tv_wake)",
        )
    return config.tv_mac.strip()
