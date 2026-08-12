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

from unittest.mock import MagicMock

from google.cloud.alloydbconnector.instrumented_socket import InstrumentedSocket
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes


class _RecordingMetricRecorder(NullMetricRecorder):
    def __init__(self) -> None:
        self.rx = 0
        self.tx = 0
        self.closed_calls: list[TelemetryAttributes] = []

    def record_bytes_rx(self, count: int) -> None:
        self.rx += count

    def record_bytes_tx(self, count: int) -> None:
        self.tx += count

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self.closed_calls.append(attrs)


def _make_isock(sock: MagicMock) -> tuple[InstrumentedSocket, _RecordingMetricRecorder]:
    mr = _RecordingMetricRecorder()
    attrs = TelemetryAttributes()
    return InstrumentedSocket(sock, mr, attrs), mr


def test_recv_records_bytes() -> None:
    sock = MagicMock()
    sock.recv.return_value = b"hello"
    isock, mr = _make_isock(sock)

    assert isock.recv(10) == b"hello"
    assert mr.rx == 5
    sock.recv.assert_called_once_with(10, 0)


def test_recv_empty_does_not_record() -> None:
    sock = MagicMock()
    sock.recv.return_value = b""
    isock, mr = _make_isock(sock)

    assert isock.recv(10) == b""
    assert mr.rx == 0


def test_recv_into_records_bytes() -> None:
    sock = MagicMock()
    sock.recv_into.return_value = 7
    isock, mr = _make_isock(sock)
    buf = bytearray(10)

    assert isock.recv_into(buf, 10) == 7
    assert mr.rx == 7


def test_recv_into_zero_does_not_record() -> None:
    sock = MagicMock()
    sock.recv_into.return_value = 0
    isock, mr = _make_isock(sock)

    assert isock.recv_into(bytearray(10)) == 0
    assert mr.rx == 0


def test_send_records_bytes() -> None:
    sock = MagicMock()
    sock.send.return_value = 4
    isock, mr = _make_isock(sock)

    assert isock.send(b"data") == 4
    assert mr.tx == 4


def test_send_zero_does_not_record() -> None:
    sock = MagicMock()
    sock.send.return_value = 0
    isock, mr = _make_isock(sock)

    assert isock.send(b"") == 0
    assert mr.tx == 0


def test_sendall_records_len_of_data() -> None:
    sock = MagicMock()
    isock, mr = _make_isock(sock)

    isock.sendall(b"hello world")
    assert mr.tx == 11
    sock.sendall.assert_called_once_with(b"hello world", 0)


def test_read_records_bytes() -> None:
    sock = MagicMock()
    sock.read.return_value = b"abcd"
    isock, mr = _make_isock(sock)

    assert isock.read(4) == b"abcd"
    assert mr.rx == 4


def test_read_empty_does_not_record() -> None:
    sock = MagicMock()
    sock.read.return_value = b""
    isock, mr = _make_isock(sock)

    assert isock.read(4) == b""
    assert mr.rx == 0


def test_write_records_bytes() -> None:
    sock = MagicMock()
    sock.write.return_value = 3
    isock, mr = _make_isock(sock)

    assert isock.write(b"abc") == 3
    assert mr.tx == 3


def test_write_zero_does_not_record() -> None:
    sock = MagicMock()
    sock.write.return_value = 0
    isock, mr = _make_isock(sock)

    assert isock.write(b"") == 0
    assert mr.tx == 0


def test_close_records_closed_connection_once() -> None:
    sock = MagicMock()
    attrs = TelemetryAttributes(dial_status="success")
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, attrs)

    isock.close()
    isock.close()  # Second close should be no-op for metric
    assert len(mr.closed_calls) == 1
    assert mr.closed_calls[0] is attrs
    assert sock.close.call_count == 2


def test_getattr_delegates_to_underlying_socket() -> None:
    sock = MagicMock()
    sock.fileno.return_value = 42
    isock, _ = _make_isock(sock)

    assert isock.fileno() == 42


def test_del_closes_if_not_closed() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.__del__()
    assert len(mr.closed_calls) == 1


def test_del_noop_if_already_closed() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.close()
    isock.__del__()
    assert len(mr.closed_calls) == 1


def test_del_swallows_exceptions() -> None:
    sock = MagicMock()
    sock.close.side_effect = RuntimeError("boom")
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    # Should not raise.
    isock.__del__()


def test_makefile_uses_instrumented_socket_for_io() -> None:
    import socket as _socket

    s1, s2 = _socket.socketpair()
    try:
        mr = _RecordingMetricRecorder()
        isock = InstrumentedSocket(s1, mr, TelemetryAttributes())

        f = isock.makefile("rwb", 0)
        try:
            s2.sendall(b"hello\n")
            assert f.read(6) == b"hello\n"
            assert mr.rx == 6

            f.write(b"world")
            f.flush()
            assert s2.recv(5) == b"world"
            assert mr.tx == 5
        finally:
            f.close()
    finally:
        s1.close()
        s2.close()
