# Copyright 2024 Google LLC
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
import logging
import time
from types import TracebackType
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
import uuid

import google.auth
from google.auth.credentials import with_scopes_if_required
import google.auth.transport.requests
import google.cloud.alloydbconnector.asyncpg as asyncpg
from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.enums import IPTypes
from google.cloud.alloydbconnector.enums import RefreshStrategy
from google.cloud.alloydbconnector.exceptions import ClosedConnectorError
from google.cloud.alloydbconnector.instance import RefreshAheadCache
from google.cloud.alloydbconnector.instance import _parse_instance_uri
from google.cloud.alloydbconnector.lazy import LazyRefreshCache
from google.cloud.alloydbconnector.telemetry import DIAL_CACHE_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import DIAL_TCP_ERROR
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


class AsyncConnector:
    """A class to configure and create connections to Cloud SQL instances
    asynchronously.

    Args:
        credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            If not specified, Application Default Credentials are used.
            These are the credentials used for authenticating with the AlloyDB
            Admin API.
        db_credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            This is only used when Auto IAM AuthN is enabled.
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
        enable_builtin_telemetry: bool = True,
    ) -> None:
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
            self._credentials, _ = google.auth.default(scopes=scopes)
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

        # check if AsyncConnector is being initialized with event loop running
        # Otherwise we will lazy init keys
        self._keys: Optional[asyncio.Task] = None
        try:
            # Try to get the running loop before creating a task. The call here
            # will raise a RuntimeError if no loop is running. Without calling
            # get_running_loop, a direct call to create_task would also raise
            # an exception but it would leak the generate_keys coroutine. To
            # avoid leaking the coroutine, we call get_running_loop first.
            asyncio.get_running_loop()
            self._keys = asyncio.create_task(generate_keys())
        except RuntimeError:
            pass
        self._client: Optional[AlloyDBClient] = None
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

    async def connect(
        self,
        instance_uri: str,
        driver: str,
        **kwargs: Any,
    ) -> Any:
        """
        Asynchronously prepares and returns a database connection object.

        Starts tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect
                with. Supported drivers are asyncpg.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        if self._closed:
            raise ClosedConnectorError(
                "Connection attempt failed because the connector has already been closed."
            )
        if self._keys is None:
            self._keys = asyncio.create_task(generate_keys())
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

        connect_func = {
            "asyncpg": asyncpg.connect,
        }
        # only accept supported database drivers
        try:
            connector = connect_func[driver]
        except KeyError:
            raise ValueError(f"Driver '{driver}' is not a supported database driver.")

        # Host and ssl options come from the certificates and instance IP
        # address so we don't want the user to specify them.
        kwargs.pop("host", None)
        kwargs.pop("ssl", None)
        kwargs.pop("port", None)

        # get connection info for AlloyDB instance
        ip_type: str | IPTypes = kwargs.pop("ip_type", self._ip_type)
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

        # callable to be used for auto IAM authn
        async def get_authentication_token() -> str:
            """Get OAuth2 access token to be used for IAM database authentication"""
            # refresh credentials if expired
            if not self._db_credentials.valid:
                request = google.auth.transport.requests.Request()
                await asyncio.to_thread(self._db_credentials.refresh, request)
            return self._db_credentials.token

        # if enable_iam_auth is set, use auth token as database password
        if enable_iam_auth:
            kwargs["password"] = get_authentication_token
        try:
            conn = await connector(
                ip_address, await conn_info.create_ssl_context(), **kwargs
            )
        except Exception:
            # The Async connector doesn't distinguish between TCP, TLS, or MDX
            # errors. So treat all errors as TCP errors.
            attrs.dial_status = DIAL_TCP_ERROR
            mr.record_dial_count(attrs)
            await cache.force_refresh()
            raise

        # record successful dial metrics
        attrs.dial_status = DIAL_SUCCESS
        latency_ms = (time.monotonic() - start_time) * 1000
        mr.record_dial_count(attrs)
        mr.record_dial_latency(latency_ms)
        return conn

    async def _remove_cached(self, instance_uri: str) -> None:
        """Stops all background refreshes and deletes the connection
        info cache from the map of caches.
        """
        logger.debug(f"['{instance_uri}']: Removing connection info from cache")
        # remove cache from stored caches and close it
        cache = self._cache.pop(instance_uri)
        await cache.close()

    async def __aenter__(self) -> AsyncConnector:
        """Enter async context manager by returning Connector object"""
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Exit async context manager by closing Connector"""
        await self.close()

    async def close(self) -> None:
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
        self._closed = True
