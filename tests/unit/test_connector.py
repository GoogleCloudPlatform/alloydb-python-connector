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

import asyncio
import socket
import ssl
import struct
from threading import Event
from threading import Thread
import time
from typing import Union

from mock import MagicMock
from mock import patch
from mocks import FakeAlloyDBClient
from mocks import FakeCredentials
from mocks import FakeCredentialsRequiresScopes
from mocks import write_static_info
import pytest

from google.api_core.exceptions import RetryError
from google.api_core.retry.retry_unary import Retry
from google.cloud.alloydbconnector import Connector
from google.cloud.alloydbconnector import IPTypes
from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.exceptions import ClosedConnectorError
from google.cloud.alloydbconnector.exceptions import IPTypeNotFoundError
from google.cloud.alloydbconnector.exceptions import MetadataExchangeError
from google.cloud.alloydbconnector.exceptions import TCPConnectionError
from google.cloud.alloydbconnector.exceptions import TLSHandshakeError
from google.cloud.alloydbconnector.instance import RefreshAheadCache
from google.cloud.alloydbconnector.instrumented_socket import InstrumentedSocket
from google.cloud.alloydbconnector.static import StaticConnectionInfoCache
from google.cloud.alloydbconnector.telemetry import DIAL_CACHE_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_MDX_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import DIAL_TCP_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_TLS_ERROR
from google.cloud.alloydbconnector.telemetry import DIAL_USER_ERROR
from google.cloud.alloydbconnector.telemetry import SHUTDOWN_TIMEOUT_MS
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.utils import generate_keys


def test_Connector_init(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets default attributes.
    """
    connector = Connector(credentials)
    assert connector._quota_project is None
    assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
    assert connector._client is None
    assert connector._credentials == credentials
    assert connector._db_credentials == credentials
    assert connector._closed is False
    connector.close()


def test_Connector_init_db_credentials(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets db_credentials when specified.
    """
    db_credentials = FakeCredentials()
    connector = Connector(credentials, db_credentials)
    assert connector._db_credentials == db_credentials
    connector.close()


def test_Connector_init_scopes() -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets the credential's scopes.
    """
    credentials = FakeCredentialsRequiresScopes()
    connector = Connector(credentials)
    assert connector._credentials != credentials
    assert connector._credentials._scopes == [
        "https://www.googleapis.com/auth/cloud-platform"
    ]
    assert connector._db_credentials != credentials
    assert connector._db_credentials._scopes == [
        "https://www.googleapis.com/auth/alloydb.login"
    ]
    connector.close()


def test_Connector_init_bad_ip_type(credentials: FakeCredentials) -> None:
    """Test that Connector errors due to bad ip_type str."""
    bad_ip_type = "BAD-IP-TYPE"
    with pytest.raises(ValueError) as exc_info:
        Connector(ip_type=bad_ip_type, credentials=credentials)
    assert (
        exc_info.value.args[0]
        == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE', 'PSC'."
    )


@pytest.mark.parametrize(
    "ip_type, expected",
    [
        (
            "private",
            IPTypes.PRIVATE,
        ),
        (
            "PRIVATE",
            IPTypes.PRIVATE,
        ),
        (
            IPTypes.PRIVATE,
            IPTypes.PRIVATE,
        ),
        (
            "public",
            IPTypes.PUBLIC,
        ),
        (
            "PUBLIC",
            IPTypes.PUBLIC,
        ),
        (
            IPTypes.PUBLIC,
            IPTypes.PUBLIC,
        ),
        (
            "psc",
            IPTypes.PSC,
        ),
        (
            "PSC",
            IPTypes.PSC,
        ),
        (
            IPTypes.PSC,
            IPTypes.PSC,
        ),
    ],
)
def test_Connector_init_ip_type(
    ip_type: Union[str, IPTypes], expected: IPTypes, credentials: FakeCredentials
) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets ip_type.
    """
    connector = Connector(credentials=credentials, ip_type=ip_type)
    assert connector._ip_type == expected
    connector.close()


def test_Connector_init_alloydb_api_endpoint_with_http_prefix(
    credentials: FakeCredentials,
) -> None:
    """
    Test to check whether the __init__ method of Connector properly sets
    alloydb_api_endpoint when its URL has an 'http://' prefix.
    """
    connector = Connector(
        alloydb_api_endpoint="http://alloydb.googleapis.com", credentials=credentials
    )
    assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
    connector.close()


def test_Connector_init_alloydb_api_endpoint_with_https_prefix(
    credentials: FakeCredentials,
) -> None:
    """
    Test to check whether the __init__ method of Connector properly sets
    alloydb_api_endpoint when its URL has an 'https://' prefix.
    """
    connector = Connector(
        alloydb_api_endpoint="https://alloydb.googleapis.com", credentials=credentials
    )
    assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
    connector.close()


def test_Connector_context_manager(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets defaults as context manager.
    """
    with Connector(credentials) as connector:
        assert connector._quota_project is None
        assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
        assert connector._client is None
        assert connector._credentials == credentials
        assert connector._db_credentials == credentials


def test_Connector_close(credentials: FakeCredentials) -> None:
    """
    Test that Connector's close method stops event loop and
    background thread, and sets the connector as closed.
    """
    with Connector(credentials) as connector:
        loop: asyncio.AbstractEventLoop = connector._loop
        thread: Thread = connector._thread
        assert loop.is_running() is True
        assert thread.is_alive() is True
        assert connector._closed is False
    assert loop.is_running() is False
    assert thread.is_alive() is False
    assert connector._closed is True


@pytest.mark.usefixtures("proxy_server")
def test_connect(credentials: FakeCredentials, fake_client: FakeAlloyDBClient) -> None:
    """
    Test that connector.connect returns connection object and refreshes
    credentials.
    """
    client = fake_client
    with Connector(credentials) as connector:
        connector._client = client
        # patch db connection creation
        with patch("google.cloud.alloydbconnector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connection = connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
            )
        # check connection is returned
        assert connection is True
        # check DB authentication refreshed the credentials
        assert connector._credentials.token
        assert connector._db_credentials.token


@pytest.mark.usefixtures("proxy_server")
def test_connect_db_credentials(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """
    Test that connector.connect refreshes only the DB credentials when
    specified.
    """
    client = fake_client
    db_credentials = FakeCredentials()
    with Connector(credentials, db_credentials) as connector:
        connector._client = client
        # patch db connection creation
        with patch("google.cloud.alloydbconnector.pg8000.connect"):
            connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
            )
        # check DB authentication refreshed only the DB credential's token
        assert not connector._credentials.token
        assert connector._db_credentials.token


def test_connect_bad_ip_type(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """Test that Connector.connect errors due to bad ip_type str."""
    with Connector(credentials=credentials) as connector:
        connector._client = fake_client
        bad_ip_type = "BAD-IP-TYPE"
        with pytest.raises(ValueError) as exc_info:
            connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
                ip_type=bad_ip_type,
            )
        assert (
            exc_info.value.args[0]
            == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE', 'PSC'."
        )


@pytest.mark.usefixtures("proxy_server")
def test_connect_psycopg(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """
    Test that connector.connect returns a connection object when using psycopg.
    """
    with Connector(credentials) as connector:
        connector._client = fake_client
        with patch("google.cloud.alloydbconnector.psycopg.connect") as mock_connect:
            mock_connect.return_value = True
            connection = connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "psycopg",
                user="test-user",
                password="test-password",
                db="test-db",
            )
        assert connection is True


def test_connect_unsupported_driver(credentials: FakeCredentials) -> None:
    """
    Test that connector.connect errors with unsupported database driver.
    """
    client = FakeAlloyDBClient()
    with Connector(credentials) as connector:
        connector._client = client
        # try to connect using unsupported driver, should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "bad_driver",
            )
        # assert custom error message for unsupported driver is present
        assert (
            exc_info.value.args[0]
            == "Driver 'bad_driver' is not a supported database driver."
        )


def test_Connector_close_called_multiple_times(credentials: FakeCredentials) -> None:
    """Test that Connector.close can be called multiple times."""
    # open and close Connector object
    connector = Connector(credentials=credentials)
    # verify background thread exists
    assert connector._thread
    connector.close()
    # check that connector thread is no longer running
    assert connector._thread.is_alive() is False
    # call connector.close a second time
    connector.close()


def test_Connector_remove_cached_bad_instance(
    credentials: FakeCredentials,
) -> None:
    """When a Connector attempts to retrieve connection info for a
    non-existent instance, it should delete the instance from
    the cache and ensure no background refresh happens (which would be
    wasted cycles).
    """
    instance_uri = "projects/test-project/locations/test-region/clusters/test-cluster/instances/bad-test-instance"
    with Connector(credentials) as connector:
        # The timeout of AlloyDB API methods is set to 60s by default.
        # We override it to 1s to shorten the duration of the test.
        connector._client = AlloyDBClient(
            "alloydb.googleapis.com", "test-project", credentials, driver="pg8000"
        )
        transport = connector._client._client.transport
        transport._wrapped_methods[transport.get_connection_info]._retry = Retry(
            timeout=1
        )
        transport._wrapped_methods[
            transport.generate_client_certificate
        ]._retry = Retry(timeout=1)

        with pytest.raises(RetryError):
            connector.connect(instance_uri, "pg8000")
        assert instance_uri not in connector._cache


async def test_Connector_remove_cached_no_ip_type(credentials: FakeCredentials) -> None:
    """When a Connector attempts to connect and preferred IP type is not present,
    it should delete the instance from the cache and ensure no background refresh
    happens (which would be wasted cycles).
    """
    instance_uri = "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance"
    # set instance to only have Public IP
    fake_client = FakeAlloyDBClient()
    fake_client.instance.ip_addrs = {"PUBLIC": "127.0.0.1"}
    with Connector(credentials=credentials) as connector:
        connector._client = fake_client
        connector._keys = asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                generate_keys(), asyncio.get_running_loop()
            ),
            loop=asyncio.get_running_loop(),
        )
        cache = RefreshAheadCache(instance_uri, fake_client, connector._keys)
        connector._cache[instance_uri] = cache
        # test instance does not have Private IP, thus should invalidate cache
        with pytest.raises(IPTypeNotFoundError):
            await connector.connect_async(instance_uri, "pg8000", ip_type="private")
        # check that cache has been removed from dict
        assert instance_uri not in connector._cache


@pytest.mark.usefixtures("proxy_server")
def test_Connector_static_connection_info(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """
    Test that Connector.__init__() can specify a static connection info to
    connect to an instance.
    """
    static_info = write_static_info(fake_client.instance)
    with Connector(credentials=credentials, static_conn_info=static_info) as connector:
        connector._client = fake_client
        # patch db connection creation
        with patch("google.cloud.alloydbconnector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connection = connector.connect(
                fake_client.instance.uri(),
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
            )
        # check connection is returned
        assert connection is True
        # check that cache is not set
        assert fake_client.instance.uri() not in connector._cache


def test_connect_when_closed(credentials: FakeCredentials) -> None:
    """
    Test that connector.connect errors when the connection is closed.
    """
    connector = Connector(credentials=credentials)
    connector.close()
    with pytest.raises(ClosedConnectorError) as exc_info:
        connector.connect("", "")
    assert (
        exc_info.value.args[0]
        == "Connection attempt failed because the connector has already been closed."
    )


def test_default_universe_domain() -> None:
    """Test that default universe domain and constructed service endpoint are
    formatted correctly.
    """
    credentials = FakeCredentials()
    credentials.token = "test-token"
    credentials.expiry = None
    with Connector(credentials=credentials) as connector:
        # test universe domain was not configured
        assert connector._universe_domain is None
        # test property and service endpoint construction
        assert connector.universe_domain == "googleapis.com"
        assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"


def test_configured_universe_domain_matches_GDU() -> None:
    """Test that configured universe domain succeeds with matched GDU credentials."""
    credentials = FakeCredentials()
    credentials.token = "test-token"
    credentials.expiry = None
    universe_domain = "googleapis.com"
    with Connector(
        credentials=credentials, universe_domain=universe_domain
    ) as connector:
        # test universe domain was configured
        assert connector._universe_domain == universe_domain
        # test property and service endpoint construction
        assert connector.universe_domain == universe_domain
        assert connector._alloydb_api_endpoint == f"alloydb.{universe_domain}"


def test_configured_universe_domain_matches_credentials() -> None:
    """Test that configured universe domain succeeds with matching universe
    domain credentials.
    """
    universe_domain = "test-universe.test"
    credentials = FakeCredentials()
    credentials.token = "test-token"
    credentials.expiry = None
    # set fake credentials to be configured for the universe domain
    credentials._universe_domain = universe_domain
    with Connector(
        credentials=credentials, universe_domain=universe_domain
    ) as connector:
        # test universe domain was configured
        assert connector._universe_domain == universe_domain
        # test property and service endpoint construction
        assert connector.universe_domain == universe_domain
        assert connector._alloydb_api_endpoint == f"alloydb.{universe_domain}"


def test_configured_universe_domain_mismatched_credentials() -> None:
    """Test that configured universe domain errors with mismatched universe
    domain credentials.
    """
    universe_domain = "test-universe.test"
    credentials = FakeCredentials()
    credentials.token = "test-token"
    credentials.expiry = None
    # credentials have GDU domain ("googleapis.com")
    with pytest.raises(ValueError) as exc_info:
        Connector(credentials=credentials, universe_domain=universe_domain)
    err_msg = (
        f"The configured universe domain ({universe_domain}) does "
        "not match the universe domain found in the credentials "
        f"({credentials.universe_domain}). If you haven't "
        "configured the universe domain explicitly, `googleapis.com` "
        "is the default."
    )
    assert exc_info.value.args[0] == err_msg


@pytest.mark.usefixtures("proxy_server")
def test_static_connection_info_dial_error_is_not_masked(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """
    Test that a failed dial against static connection info surfaces its own
    error. The static path never stores its cache, so removing the cache on
    failure must tolerate a miss rather than raising KeyError over the top of
    the real error.
    """
    static_info = write_static_info(fake_client.instance)
    with Connector(credentials=credentials, static_conn_info=static_info) as connector:
        connector._client = fake_client
        with patch.object(
            StaticConnectionInfoCache, "connect_info", side_effect=Exception("boom")
        ):
            with pytest.raises(Exception, match="boom"):
                connector.connect(fake_client.instance.uri(), "pg8000")


INSTANCE_URI = (
    "projects/test-project/locations/test-region"
    "/clusters/test-cluster/instances/test-instance"
)


class _RecordingMetricRecorder(NullMetricRecorder):
    """Captures what the connector reports for a dial attempt."""

    def __init__(self) -> None:
        self.dial_statuses: list[str] = []
        self.latencies: list[float] = []
        self.open_calls = 0
        self.closed_calls = 0

    def record_dial_count(self, attrs: TelemetryAttributes) -> None:
        self.dial_statuses.append(attrs.dial_status)

    def record_dial_latency(self, latency_ms: float) -> None:
        self.latencies.append(latency_ms)

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        self.open_calls += 1

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self.closed_calls += 1


def _connector_with_recorder(
    credentials: FakeCredentials, client: FakeAlloyDBClient
) -> tuple[Connector, _RecordingMetricRecorder]:
    # Telemetry is on so the driver gets an InstrumentedSocket; the recorder
    # below is injected directly, so no exporter is ever built.
    connector = Connector(credentials, enable_builtin_telemetry=True)
    connector._client = client
    mr = _RecordingMetricRecorder()
    connector._metric_recorders[INSTANCE_URI] = mr
    return connector, mr


@pytest.mark.usefixtures("proxy_server")
def test_connect_records_successful_dial(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """A successful dial reports success exactly once, with a latency and an
    open connection."""
    connector, mr = _connector_with_recorder(credentials, fake_client)
    with connector:
        with patch("google.cloud.alloydbconnector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connector.connect(INSTANCE_URI, "pg8000", user="u", password="p", db="d")
    assert mr.dial_statuses == [DIAL_SUCCESS]
    assert len(mr.latencies) == 1
    assert mr.open_calls == 1


@pytest.mark.parametrize(
    "error,expected_status",
    [
        (TCPConnectionError("boom"), DIAL_TCP_ERROR),
        (TLSHandshakeError("boom"), DIAL_TLS_ERROR),
        (MetadataExchangeError("boom"), DIAL_MDX_ERROR),
    ],
)
def test_connect_records_dial_error_status(
    credentials: FakeCredentials,
    fake_client: FakeAlloyDBClient,
    error: Exception,
    expected_status: str,
) -> None:
    """Each classified connect failure reports its own status, once."""
    connector, mr = _connector_with_recorder(credentials, fake_client)
    with connector:
        with patch.object(Connector, "metadata_exchange", side_effect=error):
            with pytest.raises(type(error)):
                connector.connect(
                    INSTANCE_URI, "pg8000", user="u", password="p", db="d"
                )
    assert mr.dial_statuses == [expected_status]
    assert mr.open_calls == 0


def test_connect_records_user_error_for_bad_ip_type(
    credentials: FakeCredentials,
) -> None:
    """Requesting an IP type the instance lacks is a caller mistake, not a
    failure to fetch connection info."""
    # Use a dedicated client: the fake_client fixture wraps a session-scoped
    # instance that other tests share.
    fake_client = FakeAlloyDBClient()
    fake_client.instance.ip_addrs = {"PUBLIC": "127.0.0.1"}
    connector, mr = _connector_with_recorder(credentials, fake_client)
    with connector:
        with pytest.raises(IPTypeNotFoundError):
            connector.connect(
                INSTANCE_URI,
                "pg8000",
                user="u",
                password="p",
                db="d",
                ip_type="PRIVATE",
            )
    assert mr.dial_statuses == [DIAL_USER_ERROR]
    # a failed dial must also invalidate the cache
    assert INSTANCE_URI not in connector._cache


@pytest.mark.usefixtures("proxy_server")
def test_connect_records_user_error_when_driver_fails(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    connector, mr = _connector_with_recorder(credentials, fake_client)
    with connector:
        with patch(
            "google.cloud.alloydbconnector.pg8000.connect",
            side_effect=Exception("bad password"),
        ):
            with pytest.raises(Exception, match="bad password"):
                connector.connect(
                    INSTANCE_URI, "pg8000", user="u", password="p", db="d"
                )
    assert mr.dial_statuses == [DIAL_USER_ERROR]
    assert mr.open_calls == 0


@pytest.mark.parametrize(
    "error",
    [
        socket.timeout("timed out"),
        OSError("connection reset"),
        struct.error("unpack requires a buffer of 4 bytes"),
    ],
)
def test_metadata_exchange_classifies_io_errors(
    credentials: FakeCredentials, error: Exception
) -> None:
    """Any failure past the TLS handshake belongs to the metadata exchange.
    Letting it escape unclassified would skip both the dial_count metric and
    the cache force_refresh in connect_async."""
    fake_sock = MagicMock()
    with Connector(credentials, enable_builtin_telemetry=False) as connector:
        with patch("socket.create_connection", return_value=MagicMock()):
            ctx = MagicMock()
            ctx.wrap_socket.return_value = fake_sock
            with patch.object(Connector, "_metadata_exchange", side_effect=error):
                with pytest.raises(MetadataExchangeError):
                    connector.metadata_exchange(INSTANCE_URI, "127.0.0.1", ctx, False)
    # The socket must not be leaked when the exchange fails.
    fake_sock.close.assert_called_once()


def test_connect_records_cache_error(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    connector, mr = _connector_with_recorder(credentials, fake_client)
    with connector:
        with patch.object(
            RefreshAheadCache, "connect_info", side_effect=Exception("api down")
        ):
            with pytest.raises(Exception, match="api down"):
                connector.connect(
                    INSTANCE_URI, "pg8000", user="u", password="p", db="d"
                )
    assert mr.dial_statuses == [DIAL_CACHE_ERROR]


def test_close_survives_slow_telemetry_shutdown(
    credentials: FakeCredentials,
) -> None:
    """A final metric export that uses its full budget must not surface as a
    TimeoutError out of close()."""

    class SlowProvider:
        """Stands in for a provider whose final export uses its full budget."""

        def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
            time.sleep(timeout_millis / 1000)

    connector = Connector(credentials, enable_builtin_telemetry=False)
    connector._telemetry_providers["p"] = SlowProvider()  # type: ignore[assignment]
    connector.close()
    assert connector._closed is True


def test_close_survives_hanging_telemetry_shutdown(
    credentials: FakeCredentials,
) -> None:
    """Neither the Cloud Monitoring exporter nor its write RPC guarantees it
    returns within the shutdown budget, so the telemetry shutdown bounds its
    own wait. close() itself is unbounded: bounding the whole sequence would
    let a slow cache close abandon the connector half-shut-down."""
    started = Event()
    release = Event()

    class HangingProvider:
        """Stands in for a provider whose final export never returns."""

        def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
            started.set()
            release.wait(30)

    connector = Connector(credentials, enable_builtin_telemetry=False)
    connector._telemetry_providers["p"] = HangingProvider()  # type: ignore[assignment]
    start = time.monotonic()
    try:
        # The real budget leaves room for a final export; shorten it here so
        # the test spends no longer proving the wait is bounded.
        with patch(
            "google.cloud.alloydbconnector.telemetry.TELEMETRY_SHUTDOWN_TIMEOUT_S",
            0.2,
        ):
            connector.close()
        elapsed = time.monotonic() - start

        assert started.is_set()
        assert elapsed < 3
        assert connector._closed is True
        assert connector._loop.is_running() is False
        assert connector._thread.is_alive() is False
    finally:
        # Let the abandoned executor thread finish so it does not hold up
        # interpreter shutdown.
        release.set()


@pytest.mark.usefixtures("proxy_server")
def test_connect_with_telemetry_enabled_writes_metrics(
    credentials: FakeCredentials,
    fake_client: FakeAlloyDBClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end with telemetry on: the dial is written to Cloud Monitoring
    under the instance's project, and the client is released on close."""
    writes: list = []
    client = MagicMock()
    client.common_project_path.return_value = "projects/test-project"
    client.create_service_time_series.side_effect = lambda req, **kw: writes.append(req)
    monkeypatch.setattr(
        "google.cloud.monitoring_v3.MetricServiceClient", lambda **kwargs: client
    )

    with Connector(credentials, enable_builtin_telemetry=True) as connector:
        connector._client = fake_client
        with patch("google.cloud.alloydbconnector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connector.connect(INSTANCE_URI, "pg8000", user="u", password="p", db="d")

    (request,) = writes
    assert request.name == "projects/test-project"
    metric_types = {ts.metric.type for ts in request.time_series}
    assert "alloydb.googleapis.com/client/connector/dial_count" in metric_types
    assert "alloydb.googleapis.com/client/connector/open_connections" in metric_types
    assert {ts.resource.labels["instance_id"] for ts in request.time_series} == {
        "test-instance"
    }
    # The gRPC channel opened in __init__ is released.
    assert client.close.called


def test_telemetry_providers_are_keyed_by_project(
    credentials: FakeCredentials,
) -> None:
    """The exporter writes every time series under the project it was built
    with, so instances in a second project need their own provider."""
    connector = Connector(credentials, enable_builtin_telemetry=False)
    with connector:
        with patch(
            "google.cloud.alloydbconnector.telemetry.new_telemetry_provider"
        ) as new_provider:
            new_provider.side_effect = lambda **kwargs: MagicMock(
                name=kwargs["project_id"]
            )
            connector._metric_recorder(INSTANCE_URI)
            connector._metric_recorder(INSTANCE_URI.replace("test-project", "other"))
            # A second instance in the first project reuses its provider.
            connector._metric_recorder(INSTANCE_URI.replace("test-instance", "other"))
            assert sorted(connector._telemetry_providers) == ["other", "test-project"]

    projects = sorted(call.kwargs["project_id"] for call in new_provider.call_args_list)
    assert projects == ["other", "test-project"]
    # close() shuts the providers down and drops them.
    assert connector._telemetry_providers == {}


@pytest.mark.usefixtures("proxy_server")
def test_failed_driver_connect_does_not_leave_open_connections_negative(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """Both pg8000 and psycopg close the socket when their startup sequence
    fails. Recording that close without a matching open would drive
    open_connections negative, once per failed connect."""
    connector, mr = _connector_with_recorder(credentials, fake_client)

    def failing_connect(sock: object, **kwargs: object) -> None:
        # What pg8000 and psycopg both do when startup fails.
        sock.close()  # type: ignore[attr-defined]
        raise Exception("password authentication failed")

    with connector:
        with patch("google.cloud.alloydbconnector.pg8000.connect", failing_connect):
            with pytest.raises(Exception, match="password authentication failed"):
                connector.connect(
                    INSTANCE_URI, "pg8000", user="u", password="p", db="d"
                )

    assert mr.dial_statuses == [DIAL_USER_ERROR]
    assert mr.open_calls == 0
    assert mr.closed_calls == 0


@pytest.mark.usefixtures("proxy_server")
def test_connection_close_is_recorded(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """A socket the driver took ownership of reports its close, so
    open_connections returns to zero."""
    connector, mr = _connector_with_recorder(credentials, fake_client)
    sockets: list = []

    def capturing_connect(sock: object, **kwargs: object) -> object:
        sockets.append(sock)
        return object()

    with connector:
        with patch("google.cloud.alloydbconnector.pg8000.connect", capturing_connect):
            connector.connect(INSTANCE_URI, "pg8000", user="u", password="p", db="d")
        assert mr.open_calls == 1
        assert mr.closed_calls == 0

        sockets[0].close()
        assert mr.closed_calls == 1


def test_metadata_exchange_classifies_tcp_errors(
    credentials: FakeCredentials,
) -> None:
    """A failure to reach the server is a TCP error, and keeps its errno so
    retry logic that branches on it is unaffected."""
    with Connector(credentials) as connector:
        with patch(
            "socket.create_connection", side_effect=ConnectionRefusedError(111, "nope")
        ):
            with pytest.raises(TCPConnectionError) as exc_info:
                connector.metadata_exchange(
                    INSTANCE_URI, "127.0.0.1", MagicMock(), False
                )
    assert exc_info.value.errno == 111
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)


def test_metadata_exchange_classifies_tls_errors(
    credentials: FakeCredentials,
) -> None:
    """A handshake failure is a TLS error, and does not leak the socket."""
    raw_sock = MagicMock()
    with Connector(credentials) as connector:
        with patch("socket.create_connection", return_value=raw_sock):
            ctx = MagicMock()
            ctx.wrap_socket.side_effect = ssl.SSLError("bad handshake")
            with pytest.raises(TLSHandshakeError):
                connector.metadata_exchange(INSTANCE_URI, "127.0.0.1", ctx, False)
    raw_sock.close.assert_called_once()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.usefixtures("proxy_server")
def test_socket_is_instrumented_only_when_telemetry_is_on(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient, enabled: bool
) -> None:
    """InstrumentedSocket sits between the driver and its socket for the life
    of the connection. A caller who opted out should get the socket the driver
    would have had without this feature."""
    with Connector(credentials, enable_builtin_telemetry=enabled) as connector:
        connector._client = fake_client
        with patch("google.cloud.alloydbconnector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connector.connect(INSTANCE_URI, "pg8000", user="u", password="p", db="d")
    (sock, *_), _ = mock_connect.call_args
    assert isinstance(sock, InstrumentedSocket) is enabled
