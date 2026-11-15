"""Runtime configuration loader, Pydantic v2 validation model, and stdlib .env parser."""

import ipaddress
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from lg_smtv_mcp.errors import ConfigError


def _parse_ipv4(env_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PydanticCustomError(
            "invalid_ipv4",
            "{name} is required and must be a valid IPv4 address",
            {"name": env_name},
        )
    cleaned = value.strip()
    try:
        ipaddress.IPv4Address(cleaned)
    except ValueError as exc:
        raise PydanticCustomError(
            "invalid_ipv4",
            "{name} must be a valid IPv4 address, got {ip!r}",
            {"name": env_name, "ip": cleaned},
        ) from exc
    return cleaned


def _parse_bounded_int(env_name: str, value: Any, *, min_val: int, max_val: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PydanticCustomError(
            "invalid_int",
            "{name} must be an integer between {min_val} and {max_val}, got {val!r}",
            {"name": env_name, "min_val": min_val, "max_val": max_val, "val": value},
        )
    try:
        parsed = int(value.strip()) if isinstance(value, str) else value
    except ValueError as exc:
        raise PydanticCustomError(
            "invalid_int",
            "{name} must be an integer between {min_val} and {max_val}, got {val!r}",
            {"name": env_name, "min_val": min_val, "max_val": max_val, "val": value},
        ) from exc
    if not (min_val <= parsed <= max_val):
        raise PydanticCustomError(
            "int_out_of_range",
            "{name} must be between {min_val} and {max_val}, got {val}",
            {"name": env_name, "min_val": min_val, "max_val": max_val, "val": parsed},
        )
    return parsed


class TvConfig(BaseModel):
    """Validated runtime configuration for lg-smtv-mcp."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            messages = "; ".join(err["msg"] for err in exc.errors())
            raise ConfigError(messages) from exc

    @field_validator("tv_ip", mode="before")
    @classmethod
    def _validate_tv_ip(cls, value: Any) -> str:
        return _parse_ipv4("TV_IP", value)

    @field_validator("wol_broadcast", mode="before")
    @classmethod
    def _validate_wol_broadcast(cls, value: Any) -> str:
        return _parse_ipv4("WOL_BROADCAST", value)

    @field_validator("port", mode="before")
    @classmethod
    def _validate_port(cls, value: Any) -> int:
        return _parse_bounded_int("PORT", value, min_val=1, max_val=65535)

    @field_validator("max_volume", mode="before")
    @classmethod
    def _validate_max_volume(cls, value: Any) -> int:
        return _parse_bounded_int("MAX_VOLUME", value, min_val=0, max_val=100)


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

    tv_ip = _optional_str("TV_IP") or ""
    port_raw = _optional_str("PORT")
    max_volume_raw = _optional_str("MAX_VOLUME")

    host = _optional_str("HOST") or "127.0.0.1"
    wol_broadcast = _optional_str("WOL_BROADCAST") or "255.255.255.255"
    client_key_path = _optional_path("CLIENT_KEY_PATH") or Path(".client_key.json")

    allowed_raw = os.environ.get("ALLOWED_HOSTS", "")
    allowed_hosts = tuple(part.strip() for part in allowed_raw.split(",") if part.strip())

    enable_raw_raw = (os.environ.get("ENABLE_RAW_SSAP") or "").strip().lower()
    enable_raw_ssap = enable_raw_raw in {"1", "true", "yes", "on"}

    kwargs: dict[str, Any] = {
        "tv_ip": tv_ip,
        "tv_mac": _optional_str("TV_MAC"),
        "wol_broadcast": wol_broadcast,
        "client_key_path": client_key_path,
        "ssh_key_path": _optional_path("SSH_KEY_PATH"),
        "webos_ssh_passphrase": os.environ.get("WEBOS_SSH_PASSPHRASE") or None,
        "ssh_known_hosts_path": _optional_path("SSH_KNOWN_HOSTS_PATH"),
        "host": host,
        "allowed_hosts": allowed_hosts,
        "mcp_bearer_token": _optional_str("MCP_BEARER_TOKEN"),
        "enable_raw_ssap": enable_raw_ssap,
    }
    if port_raw is not None:
        kwargs["port"] = port_raw
    if max_volume_raw is not None:
        kwargs["max_volume"] = max_volume_raw

    return TvConfig(**kwargs)


def require_bearer_token(config: TvConfig) -> str:
    """Return a non-empty MCP_BEARER_TOKEN or raise ConfigError."""
    if config.mcp_bearer_token is None or not config.mcp_bearer_token.strip():
        raise ConfigError("MCP_BEARER_TOKEN is required when running streamable-http transport")
    return config.mcp_bearer_token.strip()


def require_mac(config: TvConfig) -> str:
    """Return a non-empty TV_MAC or raise ConfigError."""
    if config.tv_mac is None or not config.tv_mac.strip():
        raise ConfigError("TV_MAC is required for Wake-on-LAN (tv_wake)")
    return config.tv_mac.strip()
