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

import errno
import ssl

from google.cloud.alloydbconnector.exceptions import TCPConnectionError
from google.cloud.alloydbconnector.exceptions import TLSHandshakeError


def test_tcp_connection_error_is_an_os_error() -> None:
    """Classifying these failures changed the type callers see. Keeping the
    stdlib base means existing `except OSError` handlers still work."""
    assert isinstance(TCPConnectionError("boom"), OSError)


def test_tls_handshake_error_is_an_ssl_error() -> None:
    assert isinstance(TLSHandshakeError("boom"), ssl.SSLError)
    assert isinstance(TLSHandshakeError("boom"), OSError)


def test_tcp_connection_error_preserves_errno() -> None:
    """Retry logic branches on errno (ECONNREFUSED vs ETIMEDOUT). Stringifying
    the original would drop it, and the concrete OSError subclass is already
    lost -- a wrapped ConnectionRefusedError no longer matches
    `except ConnectionRefusedError`."""
    original = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

    err = TCPConnectionError.from_oserror(original)

    assert err.errno == errno.ECONNREFUSED
    assert err.strerror == "Connection refused"
    assert str(err) == "[Errno 61] Connection refused" or "Connection refused" in str(
        err
    )


def test_tls_handshake_error_preserves_ssl_metadata() -> None:
    """reason is what callers key off to tell a cert failure from a protocol
    one. It is set by the _ssl C layer on the instance rather than derived
    from args, so it has to be copied across explicitly."""
    original = ssl.SSLError(1, "handshake failure")
    original.reason = "CERTIFICATE_VERIFY_FAILED"
    original.library = "SSL"

    err = TLSHandshakeError.from_sslerror(original)

    assert err.reason == "CERTIFICATE_VERIFY_FAILED"
    assert err.library == "SSL"
    assert err.errno == 1
    assert "handshake failure" in str(err)


def test_wrapped_errors_keep_the_original_as_cause() -> None:
    """The concrete type is not preserved, so __cause__ is the only way back
    to it."""
    original = TimeoutError(errno.ETIMEDOUT, "Operation timed out")
    try:
        try:
            raise original
        except OSError as e:
            raise TCPConnectionError.from_oserror(e) from e
    except TCPConnectionError as err:
        assert err.__cause__ is original
        assert err.errno == errno.ETIMEDOUT


def test_wrapping_an_error_without_errno_keeps_its_message() -> None:
    """Not every OSError/SSLError carries the (errno, strerror) pair. Rebuilding
    one from errno alone would throw the message away, and str() on a bare
    SSLError is already the repr of its args tuple, so routing through str()
    would nest that repr a level deeper."""
    assert str(TCPConnectionError.from_oserror(OSError("bare message"))) == (
        "bare message"
    )
    assert str(TLSHandshakeError.from_sslerror(ssl.SSLError("bare message"))) == (
        "bare message"
    )


def test_wrapping_a_handmade_ssl_error_does_not_raise() -> None:
    """reason and library are set by the _ssl C layer, so they are absent
    entirely on an SSLError built by hand."""
    err = TLSHandshakeError.from_sslerror(ssl.SSLError(1, "handshake failure"))
    assert err.reason is None
    assert err.library is None
