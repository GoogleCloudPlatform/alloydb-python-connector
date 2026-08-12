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

from typing import Any

from google.cloud.alloydbconnector.telemetry import MetricRecorderType
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes


class InstrumentedSocket:
    """A thin socket wrapper that tracks bytes sent/received and records

    a closed connection metric on close.

    Delegates all attribute access to the underlying socket so it can be
    used as a drop-in replacement.
    """

    def __init__(
        self,
        sock: Any,
        metric_recorder: MetricRecorderType,
        attrs: TelemetryAttributes,
    ) -> None:
        self._sock = sock
        self._mr = metric_recorder
        self._attrs = attrs
        self._closed = False

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        data = self._sock.recv(bufsize, flags)
        if data:
            self._mr.record_bytes_rx(len(data))
        return data

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
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

    def read(self, bufsize: int = 0) -> bytes:
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
        import socket

        # Explicitly call the standard library makefile function
        # passing self instead of the raw socket, so that all reads and writes will
        # call the functions above and allow for gathering telemetry.
        return socket.socket.makefile(
            self,  # type: ignore[call-overload]
            mode,
            buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._mr.record_closed_connection(self._attrs)
        self._sock.close()

    def __del__(self) -> None:
        try:
            if getattr(self, "_closed", True) is False:
                self.close()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)
