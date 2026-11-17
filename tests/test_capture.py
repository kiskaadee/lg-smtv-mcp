"""Offline contract tests for AsyncSshCaptureTransport and CaptureClient."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.capture import AsyncSshCaptureTransport, CaptureClient


class FakeSftpClient:
    def __init__(
        self,
        *,
        files: dict[str, bytes] | None = None,
        extra_names: list[str] | None = None,
        read_error: Exception | None = None,
        remove_delay: float = 0.0,
        remove_error: Exception | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.extra_names = list(extra_names or [])
        self.read_error = read_error
        self.remove_delay = remove_delay
        self.remove_error = remove_error
        self.read_calls: list[str] = []
        self.remove_calls: list[str] = []
        self.closed = False

    async def read(self, path: str) -> bytes:
        self.read_calls.append(path)
        if self.read_error is not None:
            raise self.read_error
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def remove(self, path: str) -> None:
        if self.remove_delay > 0:
            await asyncio.sleep(self.remove_delay)
        self.remove_calls.append(path)
        if self.remove_error is not None:
            raise self.remove_error
        self.files.pop(path, None)

    async def listdir(self, path: str) -> list[str]:
        _ = path
        names = {Path(p).name for p in self.files} | set(self.extra_names)
        return sorted(names)

    async def exit(self) -> None:
        self.closed = True


class FakeSshConnection:
    def __init__(self, sftp: FakeSftpClient) -> None:
        self.sftp = sftp
        self.sftp_calls = 0
        self.closed = False

    async def start_sftp_client(self) -> FakeSftpClient:
        self.sftp_calls += 1
        return self.sftp

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeCaptureTransport:
    def __init__(
        self,
        *,
        image_bytes: bytes = b"default-image",
        read_error: Exception | None = None,
        block_read: bool = False,
    ) -> None:
        self.image_bytes = image_bytes
        self.read_error = read_error
        self.block_read = block_read
        self.read_calls: list[tuple[str, float, float]] = []
        self.read_cancelled = asyncio.Event()
        self.closed = False

    async def read_and_remove(
        self,
        remote_path: str,
        timeout: float = 6.0,
        cleanup_grace: float = 1.5,
    ) -> bytes:
        self.read_calls.append((remote_path, timeout, cleanup_grace))
        if self.block_read:
            try:
                await asyncio.Event().wait()
            finally:
                self.read_cancelled.set()
        if self.read_error is not None:
            raise self.read_error
        return self.image_bytes

    async def sweep_temp_files(
        self,
        prefix: str = "/tmp/lg_smtv_cap_",
        exclude: str | None = None,
    ) -> None:
        _ = (prefix, exclude)

    async def close(self) -> None:
        self.closed = True


def _make_config(tmp_path: Path, *, with_ssh: bool = False) -> TvConfig:
    ssh_key = tmp_path / "id_rsa"
    known_hosts = tmp_path / "known_hosts"
    if with_ssh:
        ssh_key.write_text("dummy", encoding="utf-8")
        known_hosts.write_text("dummy", encoding="utf-8")
    kwargs: dict[str, Any] = {"tv_ip": "192.0.2.10"}
    if with_ssh:
        kwargs["ssh_key_path"] = ssh_key
        kwargs["ssh_known_hosts_path"] = known_hosts
        kwargs["webos_ssh_passphrase"] = "secret"
    return TvConfig(**kwargs)


async def test_async_ssh_capture_transport_connect_uses_required_args(tmp_path: Path) -> None:
    sftp = FakeSftpClient(files={"/tmp/lg_smtv_cap_1.jpg": b"jpeg"})
    conn = FakeSshConnection(sftp)
    calls: list[dict[str, Any]] = []

    async def fake_connect(**kwargs: Any) -> FakeSshConnection:
        calls.append(kwargs)
        return conn

    cfg = _make_config(tmp_path, with_ssh=True)
    transport = AsyncSshCaptureTransport(cfg, connect_fn=fake_connect)
    data = await transport.read_and_remove("/tmp/lg_smtv_cap_1.jpg")

    assert data == b"jpeg"
    assert len(calls) == 1
    assert calls[0]["host"] == "192.0.2.10"
    assert calls[0]["port"] == 9922
    assert calls[0]["username"] == "prisoner"
    assert calls[0]["config"] == []
    assert calls[0]["server_host_key_algs"] == ["ssh-rsa"]
    assert calls[0]["signature_algs"] == ["ssh-rsa"]
    assert calls[0]["client_keys"] == [str(cfg.ssh_key_path)]
    assert calls[0]["known_hosts"] == str(cfg.ssh_known_hosts_path)
    assert calls[0]["passphrase"] == "secret"


async def test_async_ssh_capture_transport_sweeps_on_initial_connect_and_reconnect(
    tmp_path: Path,
) -> None:
    sftp1 = FakeSftpClient(
        files={"/tmp/lg_smtv_cap_1.jpg": b"one"},
        extra_names=["lg_smtv_cap_old.jpg", "other.txt"],
    )
    conn1 = FakeSshConnection(sftp1)
    sftp2 = FakeSftpClient(
        files={"/tmp/lg_smtv_cap_2.jpg": b"two"},
        extra_names=["lg_smtv_cap_stale.jpg", "keep.txt"],
    )
    conn2 = FakeSshConnection(sftp2)
    conns = [conn1, conn2]

    async def fake_connect(**kwargs: Any) -> FakeSshConnection:
        _ = kwargs
        return conns.pop(0)

    transport = AsyncSshCaptureTransport(_make_config(tmp_path), connect_fn=fake_connect)
    await transport.read_and_remove("/tmp/lg_smtv_cap_1.jpg")
    assert "/tmp/lg_smtv_cap_old.jpg" in sftp1.remove_calls
    assert "/tmp/other.txt" not in sftp1.remove_calls

    # Force transport error on next read to trigger reconnect and sweep on new SFTP.
    sftp1.read_error = OSError("boom")
    with pytest.raises(OSError):
        await transport.read_and_remove("/tmp/lg_smtv_cap_1.jpg")

    data = await transport.read_and_remove("/tmp/lg_smtv_cap_2.jpg")
    assert data == b"two"
    assert "/tmp/lg_smtv_cap_stale.jpg" in sftp2.remove_calls
    assert "/tmp/keep.txt" not in sftp2.remove_calls


async def test_async_ssh_capture_transport_sweep_preserves_in_flight_remote_path(
    tmp_path: Path,
) -> None:
    remote_path = "/tmp/lg_smtv_cap_1.jpg"
    sftp = FakeSftpClient(
        files={
            remote_path: b"fresh-frame",
            "/tmp/lg_smtv_cap_stale.jpg": b"stale-frame",
        },
        extra_names=["other_file.txt"],
    )
    conn = FakeSshConnection(sftp)

    async def fake_connect(**kwargs: Any) -> FakeSshConnection:
        _ = kwargs
        return conn

    transport = AsyncSshCaptureTransport(_make_config(tmp_path), connect_fn=fake_connect)
    data = await transport.read_and_remove(remote_path)

    assert data == b"fresh-frame"
    assert "/tmp/lg_smtv_cap_stale.jpg" in sftp.remove_calls
    assert remote_path in sftp.remove_calls
    assert "/tmp/other_file.txt" not in sftp.remove_calls


async def test_async_ssh_capture_transport_closes_connection_when_start_sftp_client_fails(
    tmp_path: Path,
) -> None:
    class FailingSshConnection:
        def __init__(self) -> None:
            self.closed = False

        async def start_sftp_client(self) -> Any:
            raise OSError("sftp start failed")

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    conn = FailingSshConnection()

    async def fake_connect(**kwargs: Any) -> FailingSshConnection:
        _ = kwargs
        return conn

    transport = AsyncSshCaptureTransport(_make_config(tmp_path), connect_fn=fake_connect)
    with pytest.raises(OSError):
        await transport.read_and_remove("/tmp/lg_smtv_cap_1.jpg")

    assert conn.closed is True


async def test_async_ssh_capture_transport_enforces_read_timeout(
    tmp_path: Path,
) -> None:
    class HangingSftp(FakeSftpClient):
        async def read(self, path: str) -> bytes:
            await asyncio.sleep(10.0)
            return b"never"

    sftp = HangingSftp(files={"/tmp/lg_smtv_cap_1.jpg": b"bytes"})
    conn = FakeSshConnection(sftp)

    async def fake_connect(**kwargs: Any) -> FakeSshConnection:
        _ = kwargs
        return conn

    transport = AsyncSshCaptureTransport(_make_config(tmp_path), connect_fn=fake_connect)
    with pytest.raises(TimeoutError):
        await transport.read_and_remove("/tmp/lg_smtv_cap_1.jpg", timeout=0.05)


async def test_async_ssh_capture_transport_finishes_remove_after_cancellation(
    tmp_path: Path,
) -> None:
    remote_path = "/tmp/lg_smtv_cap_1.jpg"

    class BlockingRemoveSftp(FakeSftpClient):
        def __init__(self) -> None:
            super().__init__(files={remote_path: b"jpeg"})
            self.remove_started = asyncio.Event()
            self.allow_remove = asyncio.Event()
            self.remove_finished = asyncio.Event()

        async def remove(self, path: str) -> None:
            self.remove_started.set()
            await self.allow_remove.wait()
            await super().remove(path)
            self.remove_finished.set()

    sftp = BlockingRemoveSftp()
    conn = FakeSshConnection(sftp)

    async def fake_connect(**kwargs: Any) -> FakeSshConnection:
        _ = kwargs
        return conn

    transport = AsyncSshCaptureTransport(_make_config(tmp_path), connect_fn=fake_connect)
    read_task = asyncio.create_task(transport.read_and_remove(remote_path, cleanup_grace=0.05))
    try:
        await asyncio.wait_for(sftp.remove_started.wait(), timeout=1.0)
        read_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read_task
    finally:
        sftp.allow_remove.set()
        if not read_task.done():
            read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)

    await asyncio.wait_for(sftp.remove_finished.wait(), timeout=1.0)
    assert remote_path in sftp.remove_calls


async def test_capture_client_sends_execute_one_shot_and_returns_metadata() -> None:
    image = b"frame-bytes"
    expected_sha = hashlib.sha256(image).hexdigest()

    transport = FakeCaptureTransport(image_bytes=image)
    client = CaptureClient(transport)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((uri, payload))
        return {"returnValue": True}

    result = await client.capture_frame(ssap_request)

    assert calls == [
        (
            "ssap://com.webos.service.capture/executeOneShot",
            {"path": "/tmp/lg_smtv_cap_1.jpg", "method": "DISPLAY", "format": "JPG"},
        )
    ]
    assert transport.read_calls == [("/tmp/lg_smtv_cap_1.jpg", 6.0, 1.5)]
    assert result.image_bytes == image
    assert result.frame_sha256 == expected_sha
    assert result.changed_since_last is True
    assert result.size_bytes == len(image)


async def test_capture_client_changed_since_last_and_close_resets() -> None:
    client = CaptureClient(FakeCaptureTransport(image_bytes=b"same"))

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        return {"returnValue": True}

    first = await client.capture_frame(ssap_request)
    second = await client.capture_frame(ssap_request)
    await client.close()
    third = await client.capture_frame(ssap_request)

    assert first.changed_since_last is True
    assert second.changed_since_last is False
    assert third.changed_since_last is True


@pytest.mark.parametrize(
    "response",
    [{"returnValue": False}, {}, {"returnValue": None}],
    ids=["false", "missing", "null"],
)
async def test_capture_client_rejects_non_true_return_value_without_sftp_read(
    response: dict[str, Any],
) -> None:
    transport = FakeCaptureTransport()
    client = CaptureClient(transport)

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        return response

    with pytest.raises(TvError) as exc_info:
        await client.capture_frame(ssap_request)
    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE
    assert len(transport.read_calls) == 0


async def test_capture_client_preserves_standby_and_pairing_errors() -> None:
    client = CaptureClient(FakeCaptureTransport())

    async def standby_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        raise TvError(ErrorCode.TV_OFFLINE_STANDBY, "standby")

    with pytest.raises(TvError) as standby_exc:
        await client.capture_frame(standby_request)
    assert standby_exc.value.code is ErrorCode.TV_OFFLINE_STANDBY

    async def pairing_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        raise TvError(ErrorCode.PAIRING_REQUIRED, "pairing")

    with pytest.raises(TvError) as pairing_exc:
        await client.capture_frame(pairing_request)
    assert pairing_exc.value.code is ErrorCode.PAIRING_REQUIRED


async def test_capture_client_maps_command_failed_without_sftp_read() -> None:
    transport = FakeCaptureTransport()
    client = CaptureClient(transport)

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        raise TvError(ErrorCode.COMMAND_FAILED, "executeOneShot failed")

    with pytest.raises(TvError) as exc_info:
        await client.capture_frame(ssap_request)
    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE
    assert len(transport.read_calls) == 0


async def test_capture_client_maps_sftp_os_error_to_screenshot_unavailable() -> None:
    transport = FakeCaptureTransport(read_error=OSError("sftp read failed"))
    client = CaptureClient(transport)

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        return {"returnValue": True}

    with pytest.raises(TvError) as exc_info:
        await client.capture_frame(ssap_request)
    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE


async def test_capture_client_maps_timeout_to_screenshot_unavailable() -> None:
    client = CaptureClient(FakeCaptureTransport())

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        await asyncio.sleep(10)
        return {"returnValue": True}

    with pytest.raises(TvError) as exc_info:
        await client.capture_frame(ssap_request)
    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE


async def test_capture_client_maps_sftp_timeout_and_cancels_read() -> None:
    transport = FakeCaptureTransport(block_read=True)
    client = CaptureClient(transport)

    async def ssap_request(uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = (uri, payload)
        return {"returnValue": True}

    with pytest.raises(TvError) as exc_info:
        await asyncio.wait_for(client.capture_frame(ssap_request), timeout=7.0)

    assert exc_info.value.code is ErrorCode.SCREENSHOT_UNAVAILABLE
    assert transport.read_cancelled.is_set()
