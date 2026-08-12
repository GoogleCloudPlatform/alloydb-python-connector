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
from datetime import datetime
from datetime import timezone
from functools import partial
import io
import logging
import socket
import ssl
import struct
from threading import Thread
import time
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional
import uuid

from google.auth import default
from google.auth.credentials import TokenState
from google.auth.credentials import with_scopes_if_required
from google.auth.transport import requests
import google.cloud.alloydb_connectors_v1.proto.resources_pb2 as connectorspb
from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.enums import IPTypes
from google.cloud.alloydbconnector.enums import RefreshStrategy
from google.cloud.alloydbconnector.exceptions import ClosedConnectorError
from google.cloud.alloydbconnector.exceptions import MetadataExchangeError
from google.cloud.alloydbconnector.exceptions import TCPConnectionError
from google.cloud.alloydbconnector.exceptions import TLSHandshakeError
from google.cloud.alloydbconnector.instance import RefreshAheadCache
from google.cloud.alloydbconnector.instance import _parse_instance_uri
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
from google.cloud.alloydbconnector.telemetry import MetricRecorderType
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.telemetry import TelemetryProviderType
from google.cloud.alloydbconnector.telemetry import new_telemetry_provider
from google.cloud.alloydbconnector.types import CacheTypes
from google.cloud.alloydbconnector.utils import generate_keys
from google.cloud.alloydbconnector.utils import strip_http_prefix
from google.cloud.alloydbconnector.version import __version__

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

logger = logging.getLogger(name=__name__)

# the port the AlloyDB server-side proxy receives connections on
SERVER_PROXY_PORT = 5433
# the maximum amount of time to wait before aborting a metadata exchange
IO_TIMEOUT = 30


class Connector:
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
            the AlloyDB API endpoint. Defaults to "alloydb.googleapis.com".
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
            reports connector metrics to the
            alloydb.googleapis.com/client/connector metric prefix in
            Cloud Monitoring. These metrics help AlloyDB improve performance
            and identify client connectivity problems. Set to False to
            disable. Default: True.
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
        enable_builtin_telemetry: bool = True,
    ) -> None:
        # create event loop and start it in background thread
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._cache: dict[str, CacheTypes] = {}
        # initialize default params
        self._quota_project = quota_project
        self._alloydb_api_endpoint = strip_http_prefix(alloydb_api_endpoint)
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
        # initialize credentials for authenticating with AlloyDB Admin API
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if credentials:
            self._credentials = with_scopes_if_required(credentials, scopes=scopes)
        # otherwise use application default credentials
        else:
            self._credentials, _ = default(scopes=scopes)
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
        self._enable_builtin_telemetry = enable_builtin_telemetry
        self._client_uid = str(uuid.uuid4())
        self._metric_recorders: dict[str, MetricRecorderType] = {}
        self._telemetry_provider: Optional[TelemetryProviderType] = None
        self._monitoring_client: Optional[object] = None
        if self._enable_builtin_telemetry:
            try:
                from google.cloud.monitoring_v3 import MetricServiceClient

                self._monitoring_client = MetricServiceClient(
                    credentials=self._credentials
                )
            except Exception as e:
                logger.debug(f"Built-in metrics exporter failed to initialize: {e}")

    def _get_telemetry_provider(self, project_id: str) -> TelemetryProviderType:
        """Get or lazily create the TelemetryProvider on first connect."""
        if self._telemetry_provider is not None:
            return self._telemetry_provider
        self._telemetry_provider = new_telemetry_provider(
            enabled=self._enable_builtin_telemetry,
            project_id=project_id,
            client_uid=self._client_uid,
            version=__version__,
            monitoring_client=self._monitoring_client,
        )
        return self._telemetry_provider

    def _metric_recorder(self, instance_uri: str) -> MetricRecorderType:
        """Get or lazily create a MetricRecorder for the given instance."""
        if instance_uri in self._metric_recorders:
            return self._metric_recorders[instance_uri]
        project, region, cluster, name = _parse_instance_uri(instance_uri)
        provider = self._get_telemetry_provider(project)
        mr = provider.create_metric_recorder(
            project_id=project,
            location=region,
            cluster=cluster,
            instance=name,
        )
        self._metric_recorders[instance_uri] = mr
        return mr

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
        try:
            conn_info = await cache.connect_info()
            ip_address = conn_info.get_preferred_ip(ip_type)
        except Exception:
            # with an error from AlloyDB API call or IP type, invalidate the
            # cache and re-raise the error
            attrs.dial_status = DIAL_CACHE_ERROR
            mr.record_dial_count(attrs)
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
        except TCPConnectionError:
            attrs.dial_status = DIAL_TCP_ERROR
            mr.record_dial_count(attrs)
            await cache.force_refresh()
            raise
        except TLSHandshakeError:
            attrs.dial_status = DIAL_TLS_ERROR
            mr.record_dial_count(attrs)
            await cache.force_refresh()
            raise
        except MetadataExchangeError:
            attrs.dial_status = DIAL_MDX_ERROR
            mr.record_dial_count(attrs)
            await cache.force_refresh()
            raise
        try:
            instrumented_sock = InstrumentedSocket(sock, mr, attrs)
            connect_partial = partial(connector, instrumented_sock, **kwargs)
            conn = await self._loop.run_in_executor(None, connect_partial)
        except Exception:
            attrs.dial_status = DIAL_USER_ERROR
            mr.record_dial_count(attrs)
            await cache.force_refresh()
            raise

        # record successful dial metrics
        attrs.dial_status = DIAL_SUCCESS
        latency_ms = (time.monotonic() - start_time) * 1000
        mr.record_dial_count(attrs)
        mr.record_dial_latency(latency_ms)
        mr.record_open_connection(attrs)
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
        except OSError as e:
            raise TCPConnectionError(str(e)) from e
        try:
            sock = ctx.wrap_socket(raw_sock, server_hostname=ip_address)
        except ssl.SSLError as e:
            raw_sock.close()
            raise TLSHandshakeError(str(e)) from e
        except OSError as e:
            raw_sock.close()
            raise TCPConnectionError(str(e)) from e

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
                raise MetadataExchangeError(
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
                raise MetadataExchangeError(
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
            raise MetadataExchangeError(
                f"Metadata Exchange request has failed with error: {resp.error}"
            )

        return sock

    async def _remove_cached(self, instance_uri: str) -> None:
        """Stops all background refreshes and deletes the connection
        info cache from the map of caches.
        """
        logger.debug(f"['{instance_uri}']: Removing connection info from cache")
        # remove cache from stored caches and close it
        cache = self._cache.pop(instance_uri)
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
            # Will attempt to gracefully shut down tasks for 3s
            close_future.result(timeout=3)
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
        # shut down telemetry provider in executor to avoid blocking the
        # event loop (shutdown triggers a final gRPC export)
        if self._telemetry_provider is not None:
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, self._telemetry_provider.shutdown)
            except Exception:
                pass
