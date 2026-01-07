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
from threading import Thread
from typing import Union

from google.api_core.exceptions import RetryError
from google.api_core.retry.retry_unary import Retry
from mock import patch
from mocks import FakeAlloyDBClient
from mocks import FakeCredentials
from mocks import FakeCredentialsRequiresScopes
from mocks import write_static_info
import pytest

from google.cloud.alloydbconnector import Connector
from google.cloud.alloydbconnector import IPTypes
from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.exceptions import ClosedConnectorError
from google.cloud.alloydbconnector.exceptions import IPTypeNotFoundError
from google.cloud.alloydbconnector.instance import RefreshAheadCache
from google.cloud.alloydbconnector.utils import generate_keys


def test_Connector_init(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets default attributes.
    """
    db_credentials = FakeCredentials()
    connector = Connector(credentials, db_credentials)
    assert connector._quota_project is None
    assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
    assert connector._client is None
    assert connector._credentials == credentials
    assert connector._db_credentials == db_credentials
    assert connector._closed is False
    connector.close()


def test_Connector_init_bad_ip_type(credentials: FakeCredentials) -> None:
    """Test that Connector errors due to bad ip_type str."""
    bad_ip_type = "BAD-IP-TYPE"
    with pytest.raises(ValueError) as exc_info:
        Connector(
            ip_type=bad_ip_type, credentials=credentials, db_credentials=credentials
        )
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
    connector = Connector(
        credentials=credentials, db_credentials=credentials, ip_type=ip_type
    )
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
        alloydb_api_endpoint="http://alloydb.googleapis.com",
        credentials=credentials,
        db_credentials=credentials,
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
        alloydb_api_endpoint="https://alloydb.googleapis.com",
        credentials=credentials,
        db_credentials=credentials,
    )
    assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
    connector.close()


def test_Connector_init_scopes() -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets the credential's scopes.
    """
    credentials = FakeCredentialsRequiresScopes()
    connector = Connector(credentials, credentials)
    assert connector._credentials != credentials
    assert connector._credentials._scopes == [
        "https://www.googleapis.com/auth/cloud-platform"
    ]
    assert connector._db_credentials != credentials
    assert connector._db_credentials._scopes == [
        "https://www.googleapis.com/auth/alloydb.login"
    ]
    connector.close()


def test_Connector_context_manager(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets defaults as context manager.
    """
    with Connector(credentials, credentials) as connector:
        assert connector._quota_project is None
        assert connector._alloydb_api_endpoint == "alloydb.googleapis.com"
        assert connector._client is None
        assert connector._credentials == credentials


def test_Connector_close(credentials: FakeCredentials) -> None:
    """
    Test that Connector's close method stops event loop and
    background thread, and sets the connector as closed.
    """
    with Connector(credentials, credentials) as connector:
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
    Test that connector.connect returns connection object.
    """
    client = fake_client
    db_credentials = FakeCredentials()
    with Connector(credentials, db_credentials) as connector:
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
        # check DB authentication refreshed the DB credential's token
        assert not connector._credentials.token
        assert connector._db_credentials.token


def test_connect_bad_ip_type(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """Test that Connector.connect errors due to bad ip_type str."""
    with Connector(credentials=credentials, db_credentials=credentials) as connector:
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


def test_connect_unsupported_driver(credentials: FakeCredentials) -> None:
    """
    Test that connector.connect errors with unsupported database driver.
    """
    client = FakeAlloyDBClient()
    with Connector(credentials, credentials) as connector:
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
    connector = Connector(credentials=credentials, db_credentials=credentials)
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
    with Connector(credentials, credentials) as connector:
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
    with Connector(credentials=credentials, db_credentials=credentials) as connector:
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
    with Connector(
        credentials=credentials,
        db_credentials=credentials,
        static_conn_info=static_info,
    ) as connector:
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
    connector = Connector(credentials=credentials, db_credentials=credentials)
    connector.close()
    with pytest.raises(ClosedConnectorError) as exc_info:
        connector.connect("", "")
    assert (
        exc_info.value.args[0]
        == "Connection attempt failed because the connector has already been closed."
    )
