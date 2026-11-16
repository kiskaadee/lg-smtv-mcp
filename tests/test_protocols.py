from dataclasses import FrozenInstanceError

import pytest

from lg_smtv_mcp.tv_core.protocols import (
    CaptureResult,
    KeysResult,
    TextResult,
    TvStateSnapshot,
    TvStatus,
    WebSocketClosedError,
)


def test_websocket_closed_error_preserves_args_and_str_representation() -> None:
    err = WebSocketClosedError(1008, "Try Again Later (EWS)")

    assert isinstance(err, Exception)
    assert err.code == 1008
    assert err.reason == "Try Again Later (EWS)"
    assert err.args
    assert "1008" in str(err)
    assert "Try Again Later (EWS)" in str(err)

    err_no_reason = WebSocketClosedError(1000)
    assert err_no_reason.code == 1000
    assert err_no_reason.reason == ""
    assert "1000" in str(err_no_reason)


def test_result_dataclasses_are_frozen_and_expose_expected_fields() -> None:
    status = TvStatus(status="connected", elapsed_ms=120)
    snapshot = TvStateSnapshot(
        connected=True,
        stale=False,
        volume=15,
        muted=False,
        sound_output="tv_speaker",
        foreground_app="youtube.leanback.v4",
        last_updated=1234.5,
    )
    capture = CaptureResult(
        image_bytes=b"\xff\xd8\xff",
        frame_sha256="abc123",
        changed_since_last=True,
        size_bytes=3,
    )
    keys_res = KeysResult(keys_sent=True, count=2, capture_error="SCREENSHOT_UNAVAILABLE: off")
    text_res = TextResult(text_sent=True, enter_sent=False)

    assert status.status == "connected"
    assert snapshot.foreground_app == "youtube.leanback.v4"
    assert capture.size_bytes == 3
    assert keys_res.count == 2
    assert keys_res.capture_error == "SCREENSHOT_UNAVAILABLE: off"
    assert text_res.enter_sent is False

    with pytest.raises(FrozenInstanceError):
        status.elapsed_ms = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.volume = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        capture.size_bytes = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        keys_res.count = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        text_res.text_sent = False  # type: ignore[misc]
