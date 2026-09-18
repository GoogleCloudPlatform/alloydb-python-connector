# Copyright 2023 Google LLC
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

import ssl


class RefreshError(Exception):
    pass


class IPTypeNotFoundError(Exception):
    pass


class ClosedConnectorError(Exception):
    pass


class TCPConnectionError(OSError):
    """Raised when the TCP connection to the AlloyDB proxy server fails.

    Subclasses OSError, which is what socket failures raised before these
    errors were classified, so that existing ``except OSError`` handlers keep
    working. Build these with :func:`from_oserror` so that ``errno`` and
    ``strerror`` survive the wrapping and callers can keep branching on them.

    Note that the concrete OSError subclass is *not* preserved: a refused
    connection arrives here as a TCPConnectionError rather than a
    ConnectionRefusedError, so a handler that matched the narrower type no
    longer does. The original is always available as ``__cause__``, and
    ``errno`` distinguishes the cases.
    """

    @classmethod
    def from_oserror(cls, e: OSError) -> "TCPConnectionError":
        """Wrap an OSError, preserving errno and strerror."""
        if e.errno is None:
            # Not every OSError carries the (errno, strerror) pair; one built
            # from a bare message has both set to None, so carry the args
            # across as they are rather than throwing the message away.
            return cls(*e.args)
        return cls(e.errno, e.strerror)


class TLSHandshakeError(ssl.SSLError):
    """Raised when the TLS handshake with the AlloyDB proxy server fails.

    Subclasses ssl.SSLError, which is what the handshake raised before these
    errors were classified, so that existing ``except ssl.SSLError`` handlers
    keep working. Build these with :func:`from_sslerror` so that ``errno``,
    ``strerror``, ``reason``, and ``library`` survive the wrapping; retry
    logic keys off ``reason`` (e.g. CERTIFICATE_VERIFY_FAILED). The original
    is available as ``__cause__``.
    """

    @classmethod
    def from_sslerror(cls, e: ssl.SSLError) -> "TLSHandshakeError":
        """Wrap an ssl.SSLError, preserving the ssl module's own metadata."""
        if e.errno is None:
            # An SSLError built from a bare message has no errno/strerror.
            # Carry the args across as they are: str() on such an error is
            # already the repr of its args tuple, so going through str()
            # would nest that repr one level deeper.
            err = cls(*e.args)
        else:
            err = cls(e.errno, e.strerror)
        # reason and library are set by the _ssl C layer on the instance, so
        # they are absent entirely on an SSLError built by hand. Set them to
        # None rather than leaving them unset, so that a caller reading
        # err.reason gets None instead of an AttributeError. typeshed
        # declares both as str, which is true only for errors OpenSSL raised.
        err.reason = getattr(e, "reason", None)  # type: ignore[assignment]
        err.library = getattr(e, "library", None)  # type: ignore[assignment]
        return err

    def __str__(self) -> str:
        # ssl.SSLError renders as the repr of its args tuple unless it carries
        # the (errno, strerror) pair the ssl module itself uses. Errors
        # wrapped from a bare message carry one arg, so render just that.
        if len(self.args) == 1:
            return str(self.args[0])
        return super().__str__()


class MetadataExchangeError(Exception):
    """Raised when the metadata exchange with the AlloyDB proxy server fails.

    Any failure after the TLS handshake completes belongs to the metadata
    exchange, including socket timeouts, short reads, malformed responses,
    and OAuth2 token refresh errors.
    """
