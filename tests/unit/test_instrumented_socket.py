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


def _make_isock(sock: MagicMock) -> tuple[InstrumentedSocket, _RecordingMetricRecorder]:
    """Build a wrapper in the state a driver sees it: counted as open."""
    mr = _RecordingMetricRecorder()
    attrs = TelemetryAttributes()
    isock = InstrumentedSocket(sock, mr, attrs)
    isock.record_open_connection()
    return isock, mr


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


def test_bytes_are_reported_without_waiting_for_a_flush() -> None:
    """Counts reach the recorder immediately rather than sitting in a
    per-socket buffer until a timer fires or the socket closes. The recorder
    side is only an integer add; the OTel call happens once per export, when
    the observable counters read the running totals."""
    sock = MagicMock()
    sock.recv.return_value = b"12345"
    isock, mr = _make_isock(sock)

    for _ in range(10):
        isock.recv(5)
    assert mr.rx == 50

    # An idle socket has nothing outstanding, so closing adds nothing.
    isock.close()
    assert mr.rx == 50


def test_close_records_closed_connection_once() -> None:
    sock = MagicMock()
    attrs = TelemetryAttributes(dial_status="success")
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, attrs)
    isock.record_open_connection()

    isock.close()
    isock.close()  # Second close should be no-op for metric
    assert len(mr.closed_calls) == 1
    assert mr.closed_calls[0] is attrs
    assert sock.close.call_count == 2


def test_close_before_open_records_nothing() -> None:
    """Both pg8000 and psycopg close the socket when their startup sequence
    fails. Recording that close would decrement open_connections without a
    matching increment, driving the gauge negative."""
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.close()
    assert mr.open_calls == []
    assert mr.closed_calls == []
    # The file descriptor is still released.
    assert sock.close.call_count == 1


def test_record_open_connection_reports_the_open() -> None:
    sock = MagicMock()
    attrs = TelemetryAttributes(iam_authn=True)
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, attrs)

    isock.record_open_connection()
    assert mr.open_calls == [attrs]


def test_close_releases_socket_even_if_recording_fails() -> None:
    """Telemetry must never keep the file descriptor open."""
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    mr.record_closed_connection = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())
    isock.record_open_connection()

    with pytest.raises(RuntimeError, match="boom"):
        isock.close()
    assert sock.close.call_count == 1


def test_getattr_delegates_to_underlying_socket() -> None:
    sock = MagicMock()
    sock.fileno.return_value = 42
    isock, _ = _make_isock(sock)

    assert isock.fileno() == 42


def test_del_closes_if_not_closed() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())
    isock.record_open_connection()

    isock.__del__()
    assert len(mr.closed_calls) == 1


def test_del_noop_if_already_closed() -> None:
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())
    isock.record_open_connection()

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

            f.write(b"world")
            f.flush()
            assert s2.recv(5) == b"world"

            assert mr.rx == 6
            assert mr.tx == 5
        finally:
            f.close()
    finally:
        s1.close()
        s2.close()


def test_read_default_matches_ssl_socket() -> None:
    """ssl.SSLSocket.read defaults to 1024. Forwarding 0 makes the SSL layer
    return b"", which callers read as a spurious EOF."""
    sock = MagicMock()
    sock.read.return_value = b"payload"
    isock, mr = _make_isock(sock)

    assert isock.read() == b"payload"
    sock.read.assert_called_once_with(1024)
    assert mr.rx == 7


def test_makefile_refcounts_the_underlying_socket() -> None:
    """socket.makefile() increments self._io_refs; because it runs with the
    wrapper as self, the increment must be moved onto the real socket or the
    socket's close-deferral bookkeeping breaks."""
    import socket as socket_mod

    left, right = socket_mod.socketpair()
    try:
        isock, _ = _make_isock(left)  # type: ignore[arg-type]
        f = isock.makefile("rwb")

        assert left._io_refs == 1
        # No stale shadow copy left on the wrapper.
        assert "_io_refs" not in isock.__dict__

        right.sendall(b"ping")
        assert f.read(4) == b"ping"

        f.close()
        assert left._io_refs == 0
    finally:
        left.close()
        right.close()


def test_does_not_shadow_socket_closed_attribute() -> None:
    """socket.socket._closed is consulted by stdlib code that runs with this
    wrapper as self, so the close-tracking flag must not reuse that name."""
    sock = MagicMock()
    isock, _ = _make_isock(sock)
    assert "_closed" not in isock.__dict__
    assert isock._close_recorded is False


def test_partially_constructed_wrapper_does_not_recurse() -> None:
    """If __init__ raised before setting _sock, attribute access must raise
    AttributeError rather than recursing until the stack is exhausted."""
    isock = InstrumentedSocket.__new__(InstrumentedSocket)
    with pytest.raises(AttributeError):
        isock._close_recorded


def test_open_is_not_recorded_after_a_close() -> None:
    """A close can beat record_open_connection: for psycopg the proxy threads
    forward traffic before the driver's connect() returns, so a server reset
    in that window closes the socket first. Recording the open anyway would
    leave open_connections permanently incremented, since no second close is
    coming."""
    sock = MagicMock()
    mr = _RecordingMetricRecorder()
    isock = InstrumentedSocket(sock, mr, TelemetryAttributes())

    isock.close()
    isock.record_open_connection()

    assert mr.open_calls == []
    assert mr.closed_calls == []


def test_concurrent_closes_record_one_close() -> None:
    """The psycopg proxy closes this socket from two threads: both directions
    of _proxy.forward close both ends. The check-and-set has to be atomic or
    one connection decrements open_connections twice."""
    import threading

    for _ in range(200):
        sock = MagicMock()
        isock, mr = _make_isock(sock)

        barrier = threading.Barrier(2)

        def closer() -> None:
            barrier.wait()
            isock.close()

        threads = [threading.Thread(target=closer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(mr.open_calls) == 1
        assert len(mr.closed_calls) == 1
