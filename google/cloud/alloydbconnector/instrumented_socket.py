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

import logging
import socket
import threading
from typing import Any

from google.cloud.alloydbconnector.telemetry import MetricRecorderType
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes

logger = logging.getLogger(name=__name__)


class InstrumentedSocket:
    """A thin socket wrapper that counts bytes sent and received and tracks
    the connection in the open connections metric.

    Delegates all other attribute access to the underlying socket so it can
    be used as a drop-in replacement. Byte counts go straight to the metric
    recorder, which only adds to an integer.
    """

    # Keeps __getattr__ from recursing if __init__ never assigned _sock.
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
        # The psycopg proxy closes this socket from two threads.
        self._lock = threading.Lock()
        # True while an open has been recorded and its close has not. Not
        # named _closed, which stdlib code reads via makefile().
        self._armed = False
        self._is_closed = False

    def record_open_connection(self) -> None:
        """Count this socket as an open connection.

        Call once the driver owns the socket, so the open and its close are
        always recorded as a pair. Does nothing if the socket is already
        closed or already counted.
        """
        with self._lock:
            if self._is_closed or self._armed:
                return
            try:
                self._mr.record_open_connection(self._attrs)
            except Exception as e:
                # Telemetry must never break the dial. Staying unarmed drops
                # the matching close too, so the gauge cannot go negative.
                logger.debug(f"Failed to record an open connection: {e}")
                return
            self._armed = True

    # Size defaults match ssl.SSLSocket.

    def recv(self, bufsize: int = 1024, flags: int = 0) -> bytes:
        data = self._sock.recv(bufsize, flags)
        if data:
            self._mr.record_bytes_rx(len(data))
        return data

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        # nbytes=0 means "fill the buffer" for both socket and SSLSocket.
        n = self._sock.recv_into(buffer, nbytes, flags)
        if n > 0:
            self._mr.record_bytes_rx(n)
        return n

    def send(self, data: bytes, flags: int = 0) -> int:
        n = self._sock.send(data, flags)
        if n > 0:
            self._mr.record_bytes_tx(n)
        return n

    def sendall(self, data: bytes, flags: int = 0) -> None:
        self._sock.sendall(data, flags)
        self._mr.record_bytes_tx(len(data))

    def read(self, bufsize: int = 1024) -> bytes:
        data = self._sock.read(bufsize)
        if data:
            self._mr.record_bytes_rx(len(data))
        return data

    def write(self, data: bytes) -> int:
        n = self._sock.write(data)
        if n > 0:
            self._mr.record_bytes_tx(n)
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
        # Run the stdlib makefile with self as the socket, so the file
        # object's reads and writes go through the counting methods above.
        f = socket.socket.makefile(
            self,  # type: ignore[call-overload]
            mode,
            buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )
        # makefile() did `self._io_refs += 1`, which landed on this wrapper.
        # Move it to the real socket, whose close() relies on it to keep the
        # fd open until every file object is closed.
        self.__dict__.pop("_io_refs", None)
        try:
            self._sock._io_refs += 1
        except AttributeError:
            # Not a real socket (e.g. a test double).
            pass
        return f

    def close(self) -> None:
        try:
            with self._lock:
                self._is_closed = True
                record = self._armed
                self._armed = False
            if record:
                try:
                    self._mr.record_closed_connection(self._attrs)
                except Exception as e:
                    # Telemetry must never break teardown. Not retried, so a
                    # partial failure cannot decrement twice.
                    logger.debug(f"Failed to record a closed connection: {e}")
        finally:
            self._sock.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        # Special methods bypass __getattr__. psycopg's proxy logs this socket.
        return repr(self._sock)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)
