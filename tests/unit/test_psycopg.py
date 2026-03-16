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

import os
import socket
import sys
import threading
import types
from typing import Any

import pytest

from google.cloud.alloydbconnector.psycopg import _proxy
from google.cloud.alloydbconnector.psycopg import connect

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="Unix domain sockets (AF_UNIX) not available on this platform",
)


def _socketpair() -> tuple[socket.socket, socket.socket]:
    """Return a connected pair of Unix domain sockets."""
    return socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)


def test_connect_calls_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() imports psycopg and forwards the right keyword arguments."""
    captured: dict = {}

    class FakeConn:
        pass

    def fake_psycopg_connect(**kwargs: Any) -> FakeConn:
        captured.update(kwargs)
        return FakeConn()

    fake_module = types.ModuleType("psycopg")
    fake_module.connect = fake_psycopg_connect  # type: ignore[attr-defined]

    # Create a real socket pair so the server_sock.accept() can succeed
    client_sock, server_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

    # We also need a "ssl_sock" stand-in. Use a plain socket; the proxy
    # just calls recv/sendall so the type doesn't matter in the test.
    ssl_a, ssl_b = _socketpair()

    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_module)

    conn = connect(
        ssl_a,  # type: ignore[arg-type]
        user="alice",
        db="mydb",
        password="secret",
    )

    assert isinstance(conn, FakeConn)
    assert captured["user"] == "alice"
    assert captured["dbname"] == "mydb"
    assert captured["password"] == "secret"
    assert captured["sslmode"] == "disable"

    client_sock.close()
    server_sock.close()
    ssl_b.close()


def test_connect_raises_on_missing_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() raises ImportError when psycopg is not installed."""
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", None)  # type: ignore[assignment]

    local_a, local_b = _socketpair()

    with pytest.raises(ImportError, match="psycopg"):
        connect(local_b, user="u", db="d", password="p")  # type: ignore[arg-type]

    local_a.close()


def _make_fake_psycopg_module(captured: dict) -> types.ModuleType:
    """Return a fake psycopg module whose connect() captures kwargs into ``captured``."""

    class FakeConn:
        pass

    def fake_connect(**kw: Any) -> FakeConn:
        captured.update(kw)
        return FakeConn()

    mod = types.ModuleType("psycopg")
    mod.connect = fake_connect  # type: ignore[attr-defined]
    return mod


def test_proxy_forwards_local_to_remote() -> None:
    """_proxy() forwards bytes written to the local socket to the remote."""
    local_a, local_b = _socketpair()
    remote_a, remote_b = _socketpair()

    remote_a.settimeout(2.0)
    t = threading.Thread(target=_proxy, args=(local_b, remote_b), daemon=True)
    t.start()

    local_a.sendall(b"hello")
    received = remote_a.recv(5)

    # Trigger EOF so the proxy thread exits.
    local_a.shutdown(socket.SHUT_RDWR)
    local_a.close()
    remote_a.close()
    t.join(timeout=2)

    assert received == b"hello"


def test_proxy_forwards_remote_to_local() -> None:
    """_proxy() forwards bytes written to the remote socket to the local."""
    local_a, local_b = _socketpair()
    remote_a, remote_b = _socketpair()

    local_a.settimeout(2.0)
    t = threading.Thread(target=_proxy, args=(local_b, remote_b), daemon=True)
    t.start()

    remote_a.sendall(b"world")
    received = local_a.recv(5)

    # Trigger EOF so the proxy thread exits.
    local_a.shutdown(socket.SHUT_RDWR)
    local_a.close()
    remote_a.close()
    t.join(timeout=2)

    assert received == b"world"


def test_proxy_eof_on_local_closes_remote() -> None:
    """EOF on the local side causes _proxy() to close the remote socket."""
    local_a, local_b = _socketpair()
    remote_a, remote_b = _socketpair()

    remote_a.settimeout(2.0)
    t = threading.Thread(target=_proxy, args=(local_b, remote_b), daemon=True)
    t.start()

    # Signal EOF from the local driver side.
    local_a.shutdown(socket.SHUT_RDWR)
    local_a.close()

    # remote_a should receive EOF once remote_b is shut down by the proxy.
    eof = remote_a.recv(1)
    remote_a.close()
    t.join(timeout=2)

    assert eof == b""
    assert not t.is_alive()


def test_connect_strips_caller_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() replaces any caller-supplied sslmode with 'disable'."""
    captured: dict = {}
    mod = _make_fake_psycopg_module(captured)
    monkeypatch.setitem(sys.modules, "psycopg", mod)

    ssl_a, ssl_b = _socketpair()
    connect(ssl_a, user="u", db="d", sslmode="require")  # type: ignore[arg-type]
    ssl_b.close()

    assert captured["sslmode"] == "disable"


def test_connect_passes_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() forwards unrecognised kwargs to psycopg.connect()."""
    captured: dict = {}
    mod = _make_fake_psycopg_module(captured)
    monkeypatch.setitem(sys.modules, "psycopg", mod)

    ssl_a, ssl_b = _socketpair()
    connect(  # type: ignore[arg-type]
        ssl_a,
        user="u",
        db="d",
        connect_timeout=10,
        application_name="myapp",
    )
    ssl_b.close()

    assert captured["connect_timeout"] == 10
    assert captured["application_name"] == "myapp"


def test_connect_cleanup_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() removes the Unix socket file and tmpdir after a successful connect."""
    captured: dict = {}
    mod = _make_fake_psycopg_module(captured)
    monkeypatch.setitem(sys.modules, "psycopg", mod)

    ssl_a, ssl_b = _socketpair()
    connect(ssl_a, user="u", db="d")  # type: ignore[arg-type]
    ssl_b.close()

    tmpdir = captured["host"]
    socket_path = os.path.join(tmpdir, ".s.PGSQL.5432")
    assert not os.path.exists(socket_path)
    assert not os.path.exists(tmpdir)


def test_no_thread_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening and closing N connections must not permanently increase thread count.

    The fake psycopg actually connects to the Unix socket so _accept_and_proxy
    can proceed and both proxy threads wind down naturally when the client
    closes its end.
    """
    import time

    N = 10
    baseline = threading.active_count()

    for _ in range(N):

        def fake_connect(**kw: Any) -> object:
            # Connect to the Unix socket so _accept_and_proxy can accept(), then
            # close immediately to send EOF through the proxy, letting both proxy
            # threads unwind.
            path = os.path.join(kw["host"], f".s.PGSQL.{kw['port']}")
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.connect(path)
            c.close()
            return object()

        mod = types.ModuleType("psycopg")
        mod.connect = fake_connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg", mod)

        ssl_a, ssl_b = _socketpair()
        connect(ssl_a, user="u", db="d")  # type: ignore[arg-type]
        ssl_b.close()

    # Give daemon threads a moment to observe EOF and exit.
    time.sleep(0.2)
    assert threading.active_count() <= baseline + 2


def test_connect_cleanup_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() removes the Unix socket file and tmpdir even when psycopg.connect raises."""
    captured_host: list[str] = []

    class FakeConn:
        pass

    def failing_connect(**kwargs: Any) -> FakeConn:
        captured_host.append(kwargs["host"])
        raise RuntimeError("db unavailable")

    mod = types.ModuleType("psycopg")
    mod.connect = failing_connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", mod)

    ssl_a, ssl_b = _socketpair()
    with pytest.raises(RuntimeError, match="db unavailable"):
        connect(ssl_a, user="u", db="d")  # type: ignore[arg-type]
    ssl_b.close()

    tmpdir = captured_host[0]
    socket_path = os.path.join(tmpdir, ".s.PGSQL.5432")
    assert not os.path.exists(socket_path)
    assert not os.path.exists(tmpdir)
