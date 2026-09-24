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

import threading
from typing import Any

from google.cloud.alloydbconnector.telemetry import MetricRecorderType
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes


class InstrumentedSocket:
    """A thin socket wrapper that tracks bytes sent/received and records
    a closed connection metric on close.

    Delegates all attribute access to the underlying socket so it can be
    used as a drop-in replacement.

    Byte counts go straight to the metric recorder, which only bumps an
    integer; the OTel observable counters read those totals once per export.
    The Go connector instead accumulates per connection and flushes on a 5s
    ticker goroutine, which Python cannot afford per connection and which
    would leave an idle connection's last bytes unreported until it saw
    traffic again.

    The open/closed connection pair is only recorded once
    ``record_open_connection`` is called; see its docstring.
    """

    # Class-level default so that __getattr__ and __del__ stay well-defined
    # even if __init__ raises before assigning the instance attribute.
    _sock: Any = None

    def __init__(
        self,
        sock: Any,
        metric_recorder: MetricRecorderType,
        attrs: TelemetryAttributes,
    ) -> None:
        self._sock = sock
        self._mr = metric_recorder
        self._attrs = attrs
        # Guards the open/closed bookkeeping below. The psycopg proxy closes
        # this socket from two threads (both directions of _proxy.forward
        # close both ends), so the check-and-set has to be atomic or one
        # connection can decrement open_connections twice.
        self._lock = threading.Lock()
        # Named distinctly from socket.socket._closed, which this object must
        # not shadow: stdlib socket code runs with this wrapper as `self` via
        # makefile() and consults the real attribute.
        #
        # Starts True so that a close before record_open_connection records
        # nothing. Both pg8000 and psycopg close the socket when their
        # startup sequence fails, and a closed connection without a matching
        # open would drive open_connections negative.
        self._close_recorded = True
        self._is_closed = False

    def record_open_connection(self) -> None:
        """Count this socket as an open connection and arm the closing metric.

        Called only once the driver has taken ownership of the socket, so
        that the open and its eventual close are always recorded as a pair.

        A close can beat this call: for psycopg the proxy threads are already
        forwarding traffic before the driver's connect() returns, so a server
        reset in that window closes the socket first. Recording the open
        anyway would leave open_connections permanently incremented, because
        no second close is coming.
        """
        with self._lock:
            if self._is_closed:
                return
            self._mr.record_open_connection(self._attrs)
            self._close_recorded = False

    def _record_rx(self, count: int) -> None:
        self._mr.record_bytes_rx(count)

    def _record_tx(self, count: int) -> None:
        self._mr.record_bytes_tx(count)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        data = self._sock.recv(bufsize, flags)
        if data:
            self._record_rx(len(data))
        return data

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        # nbytes=0 is correct, not a missing None: both ssl.SSLSocket.recv_into
        # and socket.recv_into treat a non-positive length with a buffer as
        # "fill the whole buffer".
        n = self._sock.recv_into(buffer, nbytes, flags)
        if n > 0:
            self._record_rx(n)
        return n

    def send(self, data: bytes, flags: int = 0) -> int:
        n = self._sock.send(data, flags)
        if n > 0:
            self._record_tx(n)
        return n

    def sendall(self, data: bytes, flags: int = 0) -> None:
        self._sock.sendall(data, flags)
        self._record_tx(len(data))

    def read(self, bufsize: int = 1024) -> bytes:
        # Default must match ssl.SSLSocket.read. Passing 0 through makes the
        # SSL layer return b"", which callers read as a spurious EOF.
        data = self._sock.read(bufsize)
        if data:
            self._record_rx(len(data))
        return data

    def write(self, data: bytes) -> int:
        n = self._sock.write(data)
        if n > 0:
            self._record_tx(n)
        return n

    def makefile(
        self,
        mode: str = "r",
        buffering: Any = None,
        *,
        encoding: Any = None,
        errors: Any = None,
        newline: Any = None,
    ) -> Any:
        import socket

        # Explicitly call the standard library makefile function
        # passing self instead of the raw socket, so that all reads and writes will
        # call the functions above and allow for gathering telemetry.
        f = socket.socket.makefile(
            self,  # type: ignore[call-overload]
            mode,
            buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )
        # makefile() ran `self._io_refs += 1`, which read the underlying
        # socket's counter through __getattr__ but wrote the result onto this
        # wrapper. Move the increment to the real socket, where the
        # _decref_socketios that SocketIO.close() resolves through
        # __getattr__ will decrement it. Without this the socket's
        # close-deferral bookkeeping is wrong and close() can tear down the
        # fd while a buffered stream is still live.
        self.__dict__.pop("_io_refs", None)
        try:
            self._sock._io_refs += 1
        except AttributeError:
            # Not a real socket (e.g. a test double); nothing to keep in sync.
            pass
        return f

    def close(self) -> None:
        try:
            with self._lock:
                self._is_closed = True
                record = not self._close_recorded
                self._close_recorded = True
            if record:
                self._mr.record_closed_connection(self._attrs)
        finally:
            # Telemetry must never keep the file descriptor open.
            self._sock.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only runs when normal lookup fails. Guard _sock itself so
        # a partially constructed wrapper raises AttributeError instead of
        # recursing until the stack is exhausted.
        if name == "_sock":
            raise AttributeError(name)
        return getattr(self._sock, name)
