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

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from datetime import timezone
from functools import partial
import io
import logging
import socket
import struct
from threading import Thread
import time
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional

from google.auth import default
from google.auth.credentials import TokenState
from google.auth.credentials import with_scopes_if_required
from google.auth.transport import requests
import google.cloud.alloydb_connectors_v1.proto.resources_pb2 as connectorspb
from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.enums import IPTypes
from google.cloud.alloydbconnector.enums import RefreshStrategy
from google.cloud.alloydbconnector.exceptions import ClosedConnectorError
from google.cloud.alloydbconnector.instance import RefreshAheadCache
from google.cloud.alloydbconnector.instrumented_socket import InstrumentedSocket
from google.cloud.alloydbconnector.lazy import LazyRefreshCache
import google.cloud.alloydbconnector.pg8000 as pg8000
import google.cloud.alloydbconnector.psycopg as psycopg
from google.cloud.alloydbconnector.static import StaticConnectionInfoCache
from google.cloud.alloydbconnector.telemetry import DIAL_CACHE_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_MDX_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import DIAL_TCP_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_TLS_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_USER_ERROR
from google.cloud.alloydbconnector.telemetry import REFRESH_AHEAD_TYPE
from google.cloud.alloydbconnector.telemetry import REFRESH_LAZY_TYPE
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.telemetry import _TelemetryMixin
from google.cloud.alloydbconnector.types import CacheTypes
from google.cloud.alloydbconnector.utils import generate_keys
from google.cloud.alloydbconnector.utils import strip_http_prefix

if TYPE_CHECKING:
    import ssl

    from google.auth.credentials import Credentials

logger = logging.getLogger(name=__name__)

# Attribute metadata_exchange tags a failure with, naming the phase of the dial
# that raised it. Tagging the error instead of wrapping it in a
# connector-specific type keeps the exception callers see unchanged: a refused
# connection stays a ConnectionRefusedError, with its errno and traceback
# intact, and the phase still reaches the dial_count metric.
_DIAL_PHASE = "_alloydb_dial_phase"


def _tag_dial_phase(e: BaseException, phase: str) -> None:
    """Record which phase of the dial an exception came from."""
    setattr(e, _DIAL_PHASE, phase)


def _dial_phase(e: BaseException, default: str) -> str:
    """Return the phase tagged on an exception, or default if it has none."""
    phase: str = getattr(e, _DIAL_PHASE, default)
    return phase


# the port the AlloyDB server-side proxy receives connections on
SERVER_PROXY_PORT = 5433
# the maximum amount of time to wait before aborting a metadata exchange
IO_TIMEOUT = 30

_DEFAULT_UNIVERSE_DOMAIN = "googleapis.com"
_DEFAULT_ALLOYDB_API_ENDPOINT = "alloydb.googleapis.com"
_ALLOYDB_HOST_TEMPLATE = "alloydb.{universe_domain}"


class Connector(_TelemetryMixin):
    """A class to configure and create connections to Cloud SQL instances.

    Args:
        credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            If not specified, Application Default Credentials are used.
            These are the credentials used for authenticating with the AlloyDB
            Admin API.
        db_credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            If not specified, the credentials used for authenticating with the
            AlloyDB Admin API will also be used to authenticate with the DB.
            If specified, the credential's scope should be
            "https://www.googleapis.com/auth/alloydb.login".
        quota_project (str): The Project ID for an existing Google Cloud
            project. The project specified is used for quota and
            billing purposes.
            Defaults to None, picking up project from environment.
        alloydb_api_endpoint (str): Base URL to use when calling
            the AlloyDB API endpoint. Defaults to "alloydb.googleapis.com",
            this argument should only be used in development.
        universe_domain (str): The universe domain for AlloyDB API calls.
            Default: "googleapis.com".
        enable_iam_auth (bool): Enables automatic IAM database authentication.
        ip_type (str | IPTypes): Default IP type for all AlloyDB connections.
            Defaults to IPTypes.PRIVATE ("PRIVATE") for private IP connections.
        refresh_strategy (str | RefreshStrategy): The default refresh strategy
            used to refresh SSL/TLS cert and instance metadata. Can be one
            of the following: RefreshStrategy.LAZY ("LAZY") or
            RefreshStrategy.BACKGROUND ("BACKGROUND").
            Default: RefreshStrategy.BACKGROUND
        static_conn_info (io.TextIOBase): A file-like JSON object that contains
            static connection info for the StaticConnectionInfoCache.
            Defaults to None, which will not use the StaticConnectionInfoCache.
            This is a *dev-only* option and should not be used in production as
            it will result in failed connections after the client certificate
            expires.
        enable_builtin_telemetry (bool): Enable built-in telemetry that
            reports on the connector's internal operations to the
            alloydb.googleapis.com/client/connector system metric prefix.
            These metrics help AlloyDB improve performance and identify
            client connectivity problems. Presently, these metrics aren't
            public, but will be made public in the future. Set to False to
            disable the internal metric export, which is useful in
            environments where outbound metric exporting is restricted.
            Default: True.
    """

    def __init__(
        self,
        credentials: Optional[Credentials] = None,
        db_credentials: Optional[Credentials] = None,
        quota_project: Optional[str] = None,
        alloydb_api_endpoint: str = "alloydb.googleapis.com",
        enable_iam_auth: bool = False,
        ip_type: str | IPTypes = IPTypes.PRIVATE,
        user_agent: Optional[str] = None,
        refresh_strategy: str | RefreshStrategy = RefreshStrategy.BACKGROUND,
        static_conn_info: Optional[io.TextIOBase] = None,
        universe_domain: Optional[str] = None,
        enable_builtin_telemetry: bool = True,
    ) -> None:
        # create event loop and start it in background thread
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._cache: dict[str, CacheTypes] = {}
        # initialize default params
        self._quota_project = quota_project
        self._enable_iam_auth = enable_iam_auth
        # if ip_type is str, convert to IPTypes enum
        if isinstance(ip_type, str):
            ip_type = IPTypes(ip_type.upper())
        self._ip_type = ip_type
        # if refresh_strategy is str, convert to RefreshStrategy enum
        if isinstance(refresh_strategy, str):
            refresh_strategy = RefreshStrategy(refresh_strategy.upper())
        self._refresh_strategy = refresh_strategy
        self._user_agent = user_agent
        self._universe_domain: Optional[str] = universe_domain
        # construct service endpoint for AlloyDB API calls
        # if user has not overridden the endpoint, build it from universe domain
        if alloydb_api_endpoint == _DEFAULT_ALLOYDB_API_ENDPOINT:
            self._alloydb_api_endpoint = _ALLOYDB_HOST_TEMPLATE.format(
                universe_domain=self.universe_domain
            )
        else:
            # user explicitly provided a custom endpoint, use it as-is
            self._alloydb_api_endpoint = strip_http_prefix(alloydb_api_endpoint)
        # initialize credentials for authenticating with AlloyDB Admin API
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if credentials:
            self._credentials = with_scopes_if_required(credentials, scopes=scopes)
        # otherwise use application default credentials
        else:
            self._credentials, _ = default(scopes=scopes)

        # validate that the universe domain of the credentials matches the
        # universe domain of the service endpoint
        if self._credentials.universe_domain != self.universe_domain:
            raise ValueError(
                f"The configured universe domain ({self.universe_domain}) does "
                "not match the universe domain found in the credentials "
                f"({self._credentials.universe_domain}). If you haven't "
                "configured the universe domain explicitly, `googleapis.com` "
                "is the default."
            )

        # initialize credentials for authenticating with the DB
        if db_credentials:
            self._db_credentials = db_credentials
        # otherwise use the same credentials as the one for authenticating with
        # AlloyDB Admin API
        else:
            scopes = ["https://www.googleapis.com/auth/alloydb.login"]
            self._db_credentials = with_scopes_if_required(
                self._credentials, scopes=scopes
            )
        self._keys = asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(generate_keys(), self._loop),
            loop=self._loop,
        )
        self._client: Optional[AlloyDBClient] = None
        self._static_conn_info = static_conn_info
        self._closed = False
        # built-in telemetry
        self._init_telemetry(enable_builtin_telemetry, self._credentials)

    @property
    def universe_domain(self) -> str:
        return self._universe_domain or _DEFAULT_UNIVERSE_DOMAIN

    def connect(self, instance_uri: str, driver: str, **kwargs: Any) -> Any:
        """
        Prepares and returns a database DBAPI connection object.

        Starts background tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect with.
                Supported drivers are pg8000.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        if self._closed:
            raise ClosedConnectorError(
                "Connection attempt failed because the connector has already been closed."
            )
        # call async connect and wait on result
        connect_task = asyncio.run_coroutine_threadsafe(
            self.connect_async(instance_uri, driver, **kwargs), self._loop
        )
        return connect_task.result()

    async def connect_async(self, instance_uri: str, driver: str, **kwargs: Any) -> Any:
        """
        Asynchronously prepares and returns a database connection object.

        Starts tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect with.
                Supported drivers are pg8000.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        if self._client is None:
            # lazy init client as it has to be initialized in async context
            self._client = AlloyDBClient(
                self._alloydb_api_endpoint,
                self._quota_project,
                self._credentials,
                user_agent=self._user_agent,
                driver=driver,
            )
        enable_iam_auth = kwargs.pop("enable_iam_auth", self._enable_iam_auth)

        mr = self._metric_recorder(instance_uri)

        attrs = TelemetryAttributes(
            iam_authn=enable_iam_auth,
            refresh_type=(
                REFRESH_LAZY_TYPE
                if self._refresh_strategy == RefreshStrategy.LAZY
                else REFRESH_AHEAD_TYPE
            ),
        )
        start_time = time.monotonic()

        # use existing connection info if possible
        cache_hit = instance_uri in self._cache
        attrs.cache_hit = cache_hit
        if cache_hit:
            cache = self._cache[instance_uri]
        elif self._static_conn_info:
            cache = StaticConnectionInfoCache(instance_uri, self._static_conn_info)
        else:
            if self._refresh_strategy == RefreshStrategy.LAZY:
                logger.debug(
                    f"['{instance_uri}']: Refresh strategy is set to lazy refresh"
                )
                cache = LazyRefreshCache(instance_uri, self._client, self._keys, mr)
            else:
                logger.debug(
                    f"['{instance_uri}']: Refresh strategy is set to background refresh"
                )
                cache = RefreshAheadCache(instance_uri, self._client, self._keys, mr)
            self._cache[instance_uri] = cache
            logger.debug(f"['{instance_uri}']: Connection info added to cache")

        connect_func: dict[str, Callable[..., Any]] = {
            "pg8000": pg8000.connect,
            "psycopg": psycopg.connect,
        }
        # only accept supported database drivers
        try:
            connector = connect_func[driver]
        except KeyError:
            raise ValueError(f"Driver '{driver}' is not a supported database driver.")

        # Host and ssl options come from the certificates and instance IP address
        # so we don't want the user to specify them.
        kwargs.pop("host", None)
        kwargs.pop("ssl", None)
        kwargs.pop("port", None)

        # get connection info for AlloyDB instance
        ip_type: IPTypes | str = kwargs.pop("ip_type", self._ip_type)
        # if ip_type is str, convert to IPTypes enum
        if isinstance(ip_type, str):
            ip_type = IPTypes(ip_type.upper())
        # Every exit path below sets attrs.dial_status, and the finally
        # records exactly one dial_count for the attempt. Recording per
        # except-branch instead would silently drop the count for any failure
        # that does not match one of the branches.
        try:
            try:
                conn_info = await cache.connect_info()
            except Exception:
                # with an error from the AlloyDB API call, invalidate the
                # cache and re-raise the error
                attrs.dial_status = DIAL_CACHE_ERROR
                await self._remove_cached(instance_uri)
                raise
            try:
                ip_address = conn_info.get_preferred_ip(ip_type)
            except Exception:
                # Asking for an IP type the instance does not have is a
                # caller mistake, not a failure to fetch connection info.
                attrs.dial_status = DIAL_USER_ERROR
                await self._remove_cached(instance_uri)
                raise
            logger.debug(f"['{instance_uri}']: Connecting to {ip_address}:5433")

            # synchronous drivers are blocking and run using executor
            try:
                metadata_partial = partial(
                    self.metadata_exchange,
                    instance_uri,
                    ip_address,
                    await conn_info.create_ssl_context(),
                    enable_iam_auth,
                )
                sock = await self._loop.run_in_executor(None, metadata_partial)
            except Exception as e:
                # metadata_exchange tags which phase of the dial failed. An
                # untagged error is a problem with the cached connection info
                # itself, e.g. create_ssl_context on a malformed certificate.
                attrs.dial_status = _dial_phase(e, default=DIAL_CACHE_ERROR)
                await cache.force_refresh()
                raise

            try:
                # InstrumentedSocket stands between the driver and its socket
                # for the life of the connection, so it is only installed when
                # telemetry is on. A caller who opted out should not carry its
                # makefile() and close() semantics for a metric that is never
                # exported.
                #
                # Pass a copy: attrs keeps being mutated for the rest of this
                # dial, while the socket holds its attributes for the lifetime
                # of the connection.
                instrumented_sock = (
                    InstrumentedSocket(sock, mr, replace(attrs))
                    if self._enable_builtin_telemetry
                    else None
                )
                connect_partial = partial(
                    connector, instrumented_sock or sock, **kwargs
                )
                conn = await self._loop.run_in_executor(None, connect_partial)
            except Exception:
                attrs.dial_status = DIAL_USER_ERROR
                await cache.force_refresh()
                raise

            attrs.dial_status = DIAL_SUCCESS
        finally:
            mr.record_dial_count(attrs)

        # record successful dial metrics
        latency_ms = (time.monotonic() - start_time) * 1000
        mr.record_dial_latency(latency_ms)
        # The socket records the open connection itself, so that the matching
        # closed connection is only recorded for a socket that was counted as
        # open. A driver that fails its startup sequence closes the socket on
        # the way out, which would otherwise leave open_connections negative.
        if instrumented_sock is not None:
            instrumented_sock.record_open_connection()
        return conn

    def metadata_exchange(
        self,
        instance_uri: str,
        ip_address: str,
        ctx: ssl.SSLContext,
        enable_iam_auth: bool,
    ) -> ssl.SSLSocket:
        """
        Sends metadata about the connection prior to the database
        protocol taking over.

        The exchange consists of four steps:

        1. Prepare a MetadataExchangeRequest including the IAM Principal's
           OAuth2 token, the user agent, and the requested authentication type.

        2. Write the size of the message as a big endian uint32 (4 bytes) to
           the server followed by the serialized message. The length does not
           include the initial four bytes.

        3. Read a big endian uint32 (4 bytes) from the server. This is the
           MetadataExchangeResponse message length and does not include the
           initial four bytes.

        4. Parse the response using the message length in step 3. If the
           response is not OK, return the response's error. If there is no error,
           the metadata exchange has succeeded and the connection is complete.

        Args:
            ip_address (str): IP address of AlloyDB instance to connect to.
            ctx (ssl.SSLContext): Context used to create a TLS connection
                with AlloyDB instance ssl certificates.
            enable_iam_auth (bool): Flag to enable IAM database authentication.

        Returns:
            sock (ssl.SSLSocket): mTLS/SSL socket connected to AlloyDB Proxy server.
        """
        try:
            raw_sock = socket.create_connection((ip_address, SERVER_PROXY_PORT))
        except Exception as e:
            _tag_dial_phase(e, DIAL_TCP_ERROR)
            raise
        try:
            # wrap_socket detaches raw_sock and closes the file descriptor
            # itself if the handshake fails, so there is nothing to clean up
            # here.
            sock = ctx.wrap_socket(raw_sock, server_hostname=ip_address)
        except Exception as e:
            _tag_dial_phase(e, DIAL_TLS_ERROR)
            raise
        try:
            return self._metadata_exchange(sock, instance_uri, enable_iam_auth)
        except Exception as e:
            # Once the handshake is done, this socket is only reachable from
            # here, so a failed exchange has to close it. Every failure past
            # the handshake belongs to the exchange: socket timeouts, short
            # reads, malformed responses and token refresh errors included.
            sock.close()
            _tag_dial_phase(e, DIAL_MDX_ERROR)
            raise

    def _metadata_exchange(
        self,
        sock: ssl.SSLSocket,
        instance_uri: str,
        enable_iam_auth: bool,
    ) -> ssl.SSLSocket:
        """Performs the metadata exchange over an established TLS connection.

        See ``metadata_exchange`` for the protocol description.
        """
        auth_type = connectorspb.MetadataExchangeRequest.DB_NATIVE
        if enable_iam_auth:
            auth_type = connectorspb.MetadataExchangeRequest.AUTO_IAM

        # Ensure the credentials are in fact valid before proceeding.
        if not self._db_credentials.token_state == TokenState.FRESH:
            self._db_credentials.refresh(requests.Request())

        logger.debug(
            f"['{instance_uri}']: Metadata exchange started "
            f"now={datetime.now(timezone.utc).isoformat()}, "
            f"token expiration={self._db_credentials.expiry.replace(tzinfo=timezone.utc).isoformat()}, "
            f"token size={len(self._db_credentials.token)}"
        )

        # form metadata exchange request
        req = connectorspb.MetadataExchangeRequest(
            user_agent=f"{self._client._user_agent}",  # type: ignore
            auth_type=auth_type,
            oauth2_token=self._db_credentials.token,
        )

        # set I/O timeout
        sock.settimeout(IO_TIMEOUT)

        # pack big-endian unsigned integer (4 bytes)
        packed_len = struct.pack(">I", req.ByteSize())

        # send metadata message length and request message
        sock.sendall(packed_len + req.SerializeToString())

        # form metadata exchange response
        resp = connectorspb.MetadataExchangeResponse()

        # read metadata message length (4 bytes)
        message_len_buffer_size = struct.Struct(">I").size
        message_len_buffer = b""
        while message_len_buffer_size > 0:
            chunk = sock.recv(message_len_buffer_size)
            if not chunk:
                raise RuntimeError(
                    "Connection closed while getting metadata exchange length!"
                )
            message_len_buffer += chunk
            message_len_buffer_size -= len(chunk)

        (message_len,) = struct.unpack(">I", message_len_buffer)

        # read metadata exchange message
        buffer = b""
        while message_len > 0:
            chunk = sock.recv(message_len)
            if not chunk:
                raise RuntimeError(
                    "Connection closed while performing metadata exchange!"
                )
            buffer += chunk
            message_len -= len(chunk)

        # parse metadata exchange response from buffer
        resp.ParseFromString(buffer)

        # reset socket back to blocking mode
        sock.setblocking(True)

        # validate metadata exchange response
        if resp.response_code != connectorspb.MetadataExchangeResponse.OK:
            raise ValueError(
                f"Metadata Exchange request has failed with error: {resp.error}"
            )

        return sock

    async def _remove_cached(self, instance_uri: str) -> None:
        """Stops all background refreshes and deletes the connection
        info cache from the map of caches.
        """
        logger.debug(f"['{instance_uri}']: Removing connection info from cache")
        # remove cache from stored caches and close it. The static connection
        # info path never stores its cache, so a miss here is expected; a
        # KeyError would replace whatever error sent us down this path.
        cache = self._cache.pop(instance_uri, None)
        if cache is not None:
            await cache.close()

    def __enter__(self) -> "Connector":
        """Enter context manager by returning Connector object"""
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Exit context manager by closing Connector"""
        self.close()

    def close(self) -> None:
        """Close Connector by stopping tasks and releasing resources."""
        if self._loop.is_running():
            close_future = asyncio.run_coroutine_threadsafe(
                self.close_async(), loop=self._loop
            )
            # Wait for the shutdown sequence to finish. Deliberately
            # unbounded: the telemetry flush inside it is already bounded by
            # TELEMETRY_SHUTDOWN_TIMEOUT_S, and a budget covering the whole
            # sequence would let a slow cache close abandon the connector
            # half-shut-down. Matches Dialer.Close in the Go connector, which
            # bounds only the metric shutdown.
            close_future.result()
        # if background thread exists for Connector, clean it up
        if self._thread.is_alive():
            if self._loop.is_running():
                # stop event loop running in background thread
                self._loop.call_soon_threadsafe(self._loop.stop)
            # wait for thread to finish closing (i.e. loop to stop)
            self._thread.join()
        self._closed = True

    async def close_async(self) -> None:
        """Helper function to cancel RefreshAheadCaches' tasks
        and close client."""
        await asyncio.gather(*[cache.close() for cache in self._cache.values()])
        await self._shutdown_telemetry()
