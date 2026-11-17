"""AsyncSSH-backed capture transport and CaptureClient for LG webOS screenshots."""

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import asyncssh

from lg_smtv_mcp.config import TvConfig
from lg_smtv_mcp.errors import ErrorCode, TvError
from lg_smtv_mcp.tv_core.protocols import CaptureResult, CaptureTransport


class AsyncSshCaptureTransport:
    """SFTP transport for reading and removing Developer Mode screenshot captures."""

    def __init__(
        self,
        config: TvConfig,
        connect_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._config = config
        self._connect_fn = connect_fn or asyncssh.connect
        self._conn: Any = None
        self._sftp: Any = None

    async def _close_handles(self) -> None:
        sftp = self._sftp
        conn = self._conn
        self._sftp = None
        self._conn = None
        if sftp is not None:
            with suppress(Exception):
                exit_fn = getattr(sftp, "exit", None)
                if callable(exit_fn):
                    res = exit_fn()
                    if asyncio.iscoroutine(res):
                        await res
                else:
                    close_fn = getattr(sftp, "close", None)
                    if callable(close_fn):
                        res = close_fn()
                        if asyncio.iscoroutine(res):
                            await res
        if conn is not None:
            with suppress(Exception):
                conn.close()
                wait_closed = getattr(conn, "wait_closed", None)
                if callable(wait_closed):
                    res = wait_closed()
                    if asyncio.iscoroutine(res):
                        await res

    async def _ensure_sftp(self, exclude: str | None = None) -> Any:
        if self._sftp is not None:
            return self._sftp

        config = self._config
        conn = await self._connect_fn(
            host=config.tv_ip,
            port=9922,
            username="prisoner",
            config=[],
            known_hosts=(
                str(config.ssh_known_hosts_path)
                if config.ssh_known_hosts_path is not None
                else None
            ),
            server_host_key_algs=["ssh-rsa"],
            signature_algs=["ssh-rsa"],
            client_keys=[str(config.ssh_key_path)] if config.ssh_key_path is not None else None,
            passphrase=config.webos_ssh_passphrase,
        )
        try:
            sftp = await conn.start_sftp_client()
        except BaseException:
            conn.close()
            with suppress(Exception):
                wait_closed = getattr(conn, "wait_closed", None)
                if callable(wait_closed):
                    res = wait_closed()
                    if asyncio.iscoroutine(res):
                        await res
            raise

        self._conn = conn
        self._sftp = sftp
        with suppress(Exception):
            await self.sweep_temp_files("/tmp/lg_smtv_cap_", exclude=exclude)
        return self._sftp

    async def read_and_remove(
        self,
        remote_path: str,
        timeout: float = 6.0,
        cleanup_grace: float = 1.5,
    ) -> bytes:
        sftp = None
        try:
            async with asyncio.timeout(timeout):
                sftp = await self._ensure_sftp(exclude=remote_path)
                data = await sftp.read(remote_path)
                return bytes(data)
        except asyncssh.Error, OSError:
            await self._close_handles()
            raise
        finally:
            if sftp is not None:
                remove_task = asyncio.create_task(sftp.remove(remote_path))

                def _consume_exception(task: asyncio.Task[Any]) -> None:
                    with suppress(Exception):
                        _ = task.exception()

                remove_task.add_done_callback(_consume_exception)
                with suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(remove_task),
                        timeout=cleanup_grace,
                    )

    async def sweep_temp_files(
        self,
        prefix: str = "/tmp/lg_smtv_cap_",
        exclude: str | None = None,
    ) -> None:
        sftp = self._sftp
        if sftp is None:
            return
        directory = os.path.dirname(prefix) or "/tmp"
        name_prefix = os.path.basename(prefix)
        exclude_name = os.path.basename(exclude) if exclude else None
        try:
            entries = await sftp.listdir(directory)
        except Exception:
            return
        for entry in entries:
            if not isinstance(entry, str) or not entry.startswith(name_prefix):
                continue
            if exclude_name is not None and entry == exclude_name:
                continue
            remote_path = f"{directory.rstrip('/')}/{entry}"
            with suppress(Exception):
                await sftp.remove(remote_path)

    async def close(self) -> None:
        await self._close_handles()


class CaptureClient:
    """FrameCapturer that triggers executeOneShot and fetches JPEG via SFTP."""

    def __init__(self, transport: CaptureTransport) -> None:
        self._transport = transport
        self._seq = 0
        self._last_sha256: str | None = None

    async def capture_frame(
        self,
        ssap_request_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> CaptureResult:
        self._seq += 1
        remote_path = f"/tmp/lg_smtv_cap_{self._seq}.jpg"
        try:
            async with asyncio.timeout(6.0):
                try:
                    response = await ssap_request_fn(
                        "ssap://com.webos.service.capture/executeOneShot",
                        {"path": remote_path, "method": "DISPLAY", "format": "JPG"},
                    )
                except TvError as exc:
                    if exc.code in (ErrorCode.TV_OFFLINE_STANDBY, ErrorCode.PAIRING_REQUIRED):
                        raise
                    raise TvError(
                        ErrorCode.SCREENSHOT_UNAVAILABLE,
                        f"executeOneShot failed: {exc.message}",
                    ) from exc

                if response.get("returnValue") is not True:
                    raise TvError(
                        ErrorCode.SCREENSHOT_UNAVAILABLE,
                        "executeOneShot did not return returnValue true",
                    )

                try:
                    image_bytes = await self._transport.read_and_remove(
                        remote_path, timeout=6.0, cleanup_grace=1.5
                    )
                except TvError:
                    raise
                except (asyncssh.Error, OSError) as exc:
                    raise TvError(
                        ErrorCode.SCREENSHOT_UNAVAILABLE,
                        f"Failed to retrieve screenshot over SSH/SFTP: {exc}",
                    ) from exc
        except TimeoutError as exc:
            raise TvError(
                ErrorCode.SCREENSHOT_UNAVAILABLE,
                "Screenshot capture timed out after 6.0s",
            ) from exc

        frame_sha256 = hashlib.sha256(image_bytes).hexdigest()
        changed_since_last = self._last_sha256 != frame_sha256
        self._last_sha256 = frame_sha256
        return CaptureResult(
            image_bytes=image_bytes,
            frame_sha256=frame_sha256,
            changed_since_last=changed_since_last,
            size_bytes=len(image_bytes),
        )

    async def close(self) -> None:
        self._last_sha256 = None
        await self._transport.close()
