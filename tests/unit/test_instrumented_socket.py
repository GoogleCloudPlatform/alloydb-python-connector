# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
import socket
import ssl
import tempfile
import threading
from typing import Any
from unittest.mock import MagicMock

from cryptography.hazmat.primitives import serialization
from mocks import FakeInstance
import pytest

from google.cloud.alloydbconnector.instrumented_socket import InstrumentedSocket
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes


class _RecordingMetricRecorder(NullMetricRecorder):
    def __init__(self) -> None:
        self.rx = 0
        self.tx = 0
        self.open_calls: list[TelemetryAttributes] = []
        self.closed_calls: list[TelemetryAttributes] = []

    def record_bytes_rx(self, count: int) -> None:
        self.rx += count

    def record_bytes_tx(self, count: int) -> None:
        self.tx += count

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        self.open_calls.append(attrs)

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self.closed_calls.append(attrs)


def _make_isock(sock: Any) -> tuple[InstrumentedSocket, _RecordingMetricRecorder]:
    """Build a wrapper in the state a driver sees it: counted as open."""
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())
    isock.record_open_connection()
    return isock, mr


@pytest.mark.parametrize(
    "method, args, result, rx, tx",
    [
        ("recv", (10,), b"hello", 5, 0),
        ("recv_into", (bytearray(10),), 7, 7, 0),
        ("read", (4,), b"abcd", 4, 0),
        ("send", (b"data",), 4, 0, 4),
        ("write", (b"abc",), 3, 0, 3),
        ("sendall", (b"hello world",), None, 0, 11),
    ],
)
def test_io_records_bytes(
    method: str, args: tuple, result: Any, rx: int, tx: int
) -> None:
    sock = MagicMock()
    getattr(sock, method).return_value = result
    isock, mr = _make_isock(sock)

    assert getattr(isock, method)(*args) == result
    assert (mr.rx, mr.tx) == (rx, tx)


@pytest.mark.parametrize(
    "method, args, result",
    [
        ("recv", (10,), b""),
        ("recv_into", (bytearray(10),), 0),
        ("read", (4,), b""),
        ("send", (b"",), 0),
        ("write", (b"",), 0),
    ],
)
def test_empty_io_does_not_record(method: str, args: tuple, result: Any) -> None:
    sock = MagicMock()
    getattr(sock, method).return_value = result
    isock, mr = _make_isock(sock)

    assert getattr(isock, method)(*args) == result
    assert (mr.rx, mr.tx) == (0, 0)


def test_close_records_closed_connection_once() -> None:
    sock = MagicMock()
    attrs = TelemetryAttributes(dial_status="success")
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, attrs)
    isock.record_open_connection()

    isock.close()
    isock.close()
    isock.__del__()
    assert mr.closed_calls == [attrs]
    assert sock.close.call_count == 3


def test_close_before_open_records_nothing() -> None:
    """Drivers close the socket when their startup sequence fails, and a
    close that arrives before the open is recorded must not count either."""
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.close()
    isock.record_open_connection()
    assert mr.open_calls == []
    assert mr.closed_calls == []
    assert sock.close.call_count == 1


def test_repeated_record_open_connection_records_one_open() -> None:
    sock = MagicMock()
    attrs = TelemetryAttributes(iam_authn=True)
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, attrs)

    isock.record_open_connection()
    isock.record_open_connection()
    assert mr.open_calls == [attrs]

    isock.close()
    assert len(mr.closed_calls) == 1


def test_failed_close_recording_releases_socket_and_is_not_retried() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    mr.record_closed_connection = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())
    isock.record_open_connection()

    isock.close()  # does not raise
    isock.close()
    assert mr.record_closed_connection.call_count == 1
    assert sock.close.call_count == 2


def test_failed_open_recording_drops_the_close() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    mr.record_open_connection = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.record_open_connection()  # does not raise
    isock.close()
    assert mr.closed_calls == []
    assert sock.close.call_count == 1


def test_concurrent_closes_record_one_close() -> None:
    """The psycopg proxy closes this socket from two threads."""
    for _ in range(200):
        isock, mr = _make_isock(MagicMock())
        barrier = threading.Barrier(2)

        def closer() -> None:
            barrier.wait()
            isock.close()

        threads = [threading.Thread(target=closer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(mr.closed_calls) == 1


def test_del_closes_and_swallows_exceptions() -> None:
    sock = MagicMock()
    sock.close.side_effect = RuntimeError("boom")
    isock, mr = _make_isock(sock)

    isock.__del__()  # does not raise
    assert len(mr.closed_calls) == 1


def test_getattr_delegates_to_underlying_socket() -> None:
    sock = MagicMock()
    sock.fileno.return_value = 42
    isock, _ = _make_isock(sock)

    assert isock.fileno() == 42


def test_does_not_shadow_socket_closed_attribute() -> None:
    """stdlib code run via makefile() reads socket._closed on this wrapper."""
    isock, _ = _make_isock(MagicMock())
    assert "_closed" not in isock.__dict__


def test_partially_constructed_wrapper_does_not_recurse() -> None:
    isock = InstrumentedSocket.__new__(InstrumentedSocket)
    with pytest.raises(AttributeError):
        isock._armed


def test_repr_delegates_to_underlying_socket() -> None:
    left, right = socket.socketpair()
    try:
        isock, _ = _make_isock(left)
        assert repr(isock) == repr(left)
    finally:
        left.close()
        right.close()


def _tls_pair(instance: FakeInstance) -> tuple[ssl.SSLSocket, ssl.SSLSocket]:
    """Return the two ends of a real TLS connection.

    In production the wrapper wraps an ssl.SSLSocket, and a MagicMock cannot
    catch mistakes in how arguments are forwarded to it.
    """
    _, _, pem_server = instance.get_pem_certs()
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # load_cert_chain only reads from files.
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "server.pem")
        key_path = os.path.join(tmpdir, "server.key")
        with open(cert_path, "w") as cert_file:
            cert_file.write(pem_server)
        with open(key_path, "wb") as key_file:
            key_file.write(
                instance.server_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
        server_ctx.load_cert_chain(cert_path, key_path)

    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    raw_client, raw_server = socket.socketpair()
    # Bound the handshake so a failure surfaces as a timeout, not a hang.
    raw_client.settimeout(30)
    raw_server.settimeout(30)

    handshaked: dict[str, ssl.SSLSocket] = {}
    errors: list[BaseException] = []

    def handshake() -> None:
        try:
            handshaked["server"] = server_ctx.wrap_socket(raw_server, server_side=True)
        except BaseException as e:  # re-raised on the calling thread below
            errors.append(e)

    thread = threading.Thread(target=handshake)
    thread.start()
    try:
        client = client_ctx.wrap_socket(raw_client)
    finally:
        thread.join(30)
    if errors:
        raise errors[0]
    return client, handshaked["server"]


def test_recv_into_over_tls_fills_the_whole_buffer(
    fake_instance: FakeInstance,
) -> None:
    """psycopg's proxy calls recv_into(view) with no length. If nbytes=0 did
    not mean "fill the buffer", reads would come back 0 and look like EOF."""
    client, server = _tls_pair(fake_instance)
    try:
        isock, mr = _make_isock(client)

        payload = b"x" * 3000
        server.sendall(payload)

        received = bytearray()
        reads = 0
        while len(received) < len(payload):
            buf = bytearray(8192)
            n = isock.recv_into(memoryview(buf))
            assert n > 0, "recv_into returned 0; the proxy would read it as EOF"
            received += buf[:n]
            reads += 1
            # TLS may split the payload across records, but a buffer-sized
            # read must not be truncated to a handful of bytes.
            assert reads <= 4, f"{len(received)} bytes took {reads} reads"

        assert bytes(received) == payload
        assert mr.rx == len(payload)
    finally:
        client.close()
        server.close()


def test_recv_and_read_over_tls_use_the_ssl_defaults(
    fake_instance: FakeInstance,
) -> None:
    """Forwarding a size of 0 to SSLSocket.read returns b"", a false EOF."""
    client, server = _tls_pair(fake_instance)
    try:
        isock, mr = _make_isock(client)

        server.sendall(b"recv-payload")
        assert isock.recv() == b"recv-payload"

        server.sendall(b"read-payload")
        assert isock.read() == b"read-payload"

        assert mr.rx == len(b"recv-payload") + len(b"read-payload")
    finally:
        client.close()
        server.close()


def test_makefile_over_tls_counts_driver_io(fake_instance: FakeInstance) -> None:
    """pg8000 does all of its I/O through sock.makefile(mode="rwb")."""
    client, server = _tls_pair(fake_instance)
    try:
        isock, mr = _make_isock(client)

        f = isock.makefile(mode="rwb")
        try:
            server.sendall(b"hello\n")
            assert f.read(6) == b"hello\n"

            f.write(b"world")
            f.flush()
            assert server.recv(5) == b"world"

            assert mr.rx == 6
            assert mr.tx == 5
            # The refcount landed on the real socket, not on the wrapper.
            assert client._io_refs == 1  # type: ignore[attr-defined]
            assert "_io_refs" not in isock.__dict__
        finally:
            f.close()
        assert client._io_refs == 0  # type: ignore[attr-defined]
    finally:
        client.close()
        server.close()
