from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest

from lg_smtv_mcp.config import TvConfig, load_config, require_bearer_token, require_mac
from lg_smtv_mcp.errors import ErrorCode, TvError


def _clear_tv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TV_IP",
        "TV_MAC",
        "WOL_BROADCAST",
        "CLIENT_KEY_PATH",
        "SSH_KEY_PATH",
        "WEBOS_SSH_PASSPHRASE",
        "SSH_KNOWN_HOSTS_PATH",
        "HOST",
        "PORT",
        "ALLOWED_HOSTS",
        "MCP_BEARER_TOKEN",
        "MAX_VOLUME",
        "ENABLE_RAW_SSAP",
    ):
        monkeypatch.delenv(key, raising=False)


def test_error_code_and_tv_error() -> None:
    expected = {
        "TV_OFFLINE_STANDBY",
        "PAIRING_REQUIRED",
        "SCREENSHOT_UNAVAILABLE",
        "LOCK_TIMEOUT",
        "WAKE_TIMEOUT",
        "COMMAND_FAILED",
        "INVALID_ARGUMENT",
    }
    assert issubclass(ErrorCode, StrEnum)
    assert {member.value for member in ErrorCode} == expected

    err = TvError(ErrorCode.INVALID_ARGUMENT, "bad input")
    assert isinstance(err, Exception)
    assert err.code is ErrorCode.INVALID_ARGUMENT
    assert err.message == "bad input"
    assert "bad input" in str(err)


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")

    cfg = load_config(env_path=tmp_path / "missing.env")

    assert isinstance(cfg, TvConfig)
    assert cfg.tv_ip == "192.0.2.10"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.max_volume == 40
    assert cfg.wol_broadcast == "255.255.255.255"
    assert cfg.client_key_path == Path(".client_key.json")
    assert cfg.enable_raw_ssap is False
    assert cfg.ssh_key_path is None
    assert cfg.webos_ssh_passphrase is None
    assert cfg.ssh_known_hosts_path is None
    assert cfg.allowed_hosts == ()
    assert cfg.tv_mac is None
    assert cfg.mcp_bearer_token is None


def test_dotenv_fallback_does_not_overwrite_existing_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_tv_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\nTV_IP=192.0.2.20\nPORT='9000'\nHOST=\"0.0.0.0\"\nENABLE_RAW_SSAP=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TV_IP", "192.0.2.99")

    cfg = load_config(env_path=env_file)

    assert cfg.tv_ip == "192.0.2.99"
    assert cfg.port == 9000
    assert cfg.host == "0.0.0.0"
    assert cfg.enable_raw_ssap is True


@pytest.mark.parametrize(
    ("env_overrides", "expected_substr"),
    [
        ({}, "TV_IP"),
        ({"TV_IP": "   "}, "TV_IP"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "-1"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "101"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "loud"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "PORT": "0"}, "PORT"),
        ({"TV_IP": "192.0.2.10", "PORT": "65536"}, "PORT"),
        ({"TV_IP": "192.0.2.10", "PORT": "abc"}, "PORT"),
    ],
)
def test_load_config_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_overrides: dict[str, str],
    expected_substr: str,
) -> None:
    _clear_tv_env(monkeypatch)
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(TvError) as exc_info:
        load_config(env_path=tmp_path / "missing.env")

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT
    assert expected_substr in exc_info.value.message


def test_allowed_hosts_comma_separated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    monkeypatch.setenv("ALLOWED_HOSTS", "tv.local, 192.0.2.5:8765 , , localhost")

    cfg = load_config(env_path=tmp_path / "missing.env")

    assert cfg.allowed_hosts == ("tv.local", "192.0.2.5:8765", "localhost")


@pytest.mark.parametrize("token", [None, "", "   "])
def test_require_bearer_token_rejects_missing_or_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token: str | None
) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    if token is not None:
        monkeypatch.setenv("MCP_BEARER_TOKEN", token)

    cfg = load_config(env_path=tmp_path / "missing.env")
    with pytest.raises(TvError) as exc_info:
        require_bearer_token(cfg)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


def test_require_bearer_token_returns_trimmed_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "  secret-token  ")

    cfg = load_config(env_path=tmp_path / "missing.env")
    assert require_bearer_token(cfg) == "secret-token"


@pytest.mark.parametrize("mac", [None, "", "   "])
def test_require_mac_rejects_missing_or_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mac: str | None
) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    if mac is not None:
        monkeypatch.setenv("TV_MAC", mac)

    cfg = load_config(env_path=tmp_path / "missing.env")
    with pytest.raises(TvError) as exc_info:
        require_mac(cfg)

    assert exc_info.value.code is ErrorCode.INVALID_ARGUMENT


def test_require_mac_returns_trimmed_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    monkeypatch.setenv("TV_MAC", " AA:BB:CC:DD:EE:FF ")

    cfg = load_config(env_path=tmp_path / "missing.env")
    assert require_mac(cfg) == "AA:BB:CC:DD:EE:FF"
