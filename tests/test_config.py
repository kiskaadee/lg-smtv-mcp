from enum import StrEnum
from pathlib import Path

import pytest

from lg_smtv_mcp.config import TvConfig, load_config, require_bearer_token, require_mac
from lg_smtv_mcp.errors import ConfigError, ErrorCode, TvError


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
        "CAPTURE_MAX_WIDTH",
        "CAPTURE_JPEG_QUALITY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_error_taxonomy_defines_codes_and_decouples_config_error() -> None:
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

    cfg_err = ConfigError("invalid TV_IP")
    assert isinstance(cfg_err, Exception)
    assert not isinstance(cfg_err, TvError)
    assert cfg_err.message == "invalid TV_IP"
    assert str(cfg_err) == "invalid TV_IP"


def test_load_config_uses_expected_defaults_when_optional_vars_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert cfg.capture_max_width is None
    assert cfg.capture_jpeg_quality is None


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
        ({"TV_IP": "999.999.999.999"}, "TV_IP"),
        ({"TV_IP": "192.0.2.10", "WOL_BROADCAST": "not-an-ip"}, "WOL_BROADCAST"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "-1"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "101"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "MAX_VOLUME": "loud"}, "MAX_VOLUME"),
        ({"TV_IP": "192.0.2.10", "PORT": "0"}, "PORT"),
        ({"TV_IP": "192.0.2.10", "PORT": "65536"}, "PORT"),
        ({"TV_IP": "192.0.2.10", "PORT": "abc"}, "PORT"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_MAX_WIDTH": "63"}, "CAPTURE_MAX_WIDTH"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_MAX_WIDTH": "3841"}, "CAPTURE_MAX_WIDTH"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_MAX_WIDTH": "wide"}, "CAPTURE_MAX_WIDTH"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_JPEG_QUALITY": "0"}, "CAPTURE_JPEG_QUALITY"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_JPEG_QUALITY": "96"}, "CAPTURE_JPEG_QUALITY"),
        ({"TV_IP": "192.0.2.10", "CAPTURE_JPEG_QUALITY": "high"}, "CAPTURE_JPEG_QUALITY"),
    ],
)
def test_load_config_rejects_invalid_env_values_with_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_overrides: dict[str, str],
    expected_substr: str,
) -> None:
    _clear_tv_env(monkeypatch)
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(ConfigError) as exc_info:
        load_config(env_path=tmp_path / "missing.env")

    assert expected_substr in exc_info.value.message


@pytest.mark.parametrize(
    ("kwargs", "expected_substr"),
    [
        ({"tv_ip": ""}, "TV_IP"),
        ({"tv_ip": "not-an-ip"}, "TV_IP"),
        ({"tv_ip": "192.0.2.10", "port": 70000}, "PORT"),
        ({"tv_ip": "192.0.2.10", "port": 8000.5}, "PORT"),
        ({"tv_ip": "192.0.2.10", "max_volume": 150}, "MAX_VOLUME"),
        ({"tv_ip": "192.0.2.10", "max_volume": 12.5}, "MAX_VOLUME"),
        ({"tv_ip": "192.0.2.10", "wol_broadcast": "bad-ip"}, "WOL_BROADCAST"),
        ({"tv_ip": "192.0.2.10", "capture_max_width": 63}, "CAPTURE_MAX_WIDTH"),
        ({"tv_ip": "192.0.2.10", "capture_max_width": 3841}, "CAPTURE_MAX_WIDTH"),
        ({"tv_ip": "192.0.2.10", "capture_max_width": 1280.5}, "CAPTURE_MAX_WIDTH"),
        ({"tv_ip": "192.0.2.10", "capture_max_width": True}, "CAPTURE_MAX_WIDTH"),
        ({"tv_ip": "192.0.2.10", "capture_jpeg_quality": 0}, "CAPTURE_JPEG_QUALITY"),
        ({"tv_ip": "192.0.2.10", "capture_jpeg_quality": 96}, "CAPTURE_JPEG_QUALITY"),
        ({"tv_ip": "192.0.2.10", "capture_jpeg_quality": 75.5}, "CAPTURE_JPEG_QUALITY"),
        ({"tv_ip": "192.0.2.10", "capture_jpeg_quality": False}, "CAPTURE_JPEG_QUALITY"),
    ],
)
def test_tv_config_rejects_invalid_direct_arguments_with_config_error(
    kwargs: dict[str, object],
    expected_substr: str,
) -> None:
    with pytest.raises(ConfigError) as exc_info:
        TvConfig(**kwargs)  # type: ignore[arg-type]

    assert expected_substr in exc_info.value.message


def test_load_config_parses_comma_separated_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    with pytest.raises(ConfigError) as exc_info:
        require_bearer_token(cfg)

    assert "MCP_BEARER_TOKEN" in exc_info.value.message


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
    with pytest.raises(ConfigError) as exc_info:
        require_mac(cfg)

    assert "TV_MAC" in exc_info.value.message


def test_require_mac_returns_trimmed_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    monkeypatch.setenv("TV_MAC", " AA:BB:CC:DD:EE:FF ")

    cfg = load_config(env_path=tmp_path / "missing.env")
    assert require_mac(cfg) == "AA:BB:CC:DD:EE:FF"


@pytest.mark.parametrize(
    ("width_env", "quality_env", "expected_width", "expected_quality"),
    [
        ("64", "1", 64, 1),
        ("3840", "95", 3840, 95),
        (" 1280 ", " 75 ", 1280, 75),
        ("", "   ", None, None),
    ],
)
def test_load_config_parses_capture_max_width_and_jpeg_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    width_env: str,
    quality_env: str,
    expected_width: int | None,
    expected_quality: int | None,
) -> None:
    _clear_tv_env(monkeypatch)
    monkeypatch.setenv("TV_IP", "192.0.2.10")
    monkeypatch.setenv("CAPTURE_MAX_WIDTH", width_env)
    monkeypatch.setenv("CAPTURE_JPEG_QUALITY", quality_env)

    cfg = load_config(env_path=tmp_path / "missing.env")

    assert cfg.capture_max_width == expected_width
    assert cfg.capture_jpeg_quality == expected_quality


def test_tv_config_accepts_explicit_none_and_valid_capture_bounds() -> None:
    cfg_none = TvConfig(tv_ip="192.0.2.10", capture_max_width=None, capture_jpeg_quality=None)
    assert cfg_none.capture_max_width is None
    assert cfg_none.capture_jpeg_quality is None

    cfg_bounds = TvConfig(tv_ip="192.0.2.10", capture_max_width=1280, capture_jpeg_quality=80)
    assert cfg_bounds.capture_max_width == 1280
    assert cfg_bounds.capture_jpeg_quality == 80
