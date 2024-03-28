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

import asyncio
from typing import Union

from mock import patch
from mocks import FakeAlloyDBClient
from mocks import FakeConnectionInfo
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector import AsyncConnector
from google.cloud.alloydb.connector import IPTypes

ALLOYDB_API_ENDPOINT = "https://alloydb.googleapis.com"


@pytest.mark.asyncio
async def test_AsyncConnector_init(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of AsyncConnector
    properly sets default attributes.
    """
    connector = AsyncConnector(credentials)
    assert connector._quota_project is None
    assert connector._alloydb_api_endpoint == ALLOYDB_API_ENDPOINT
    assert connector._client is None
    assert connector._credentials == credentials
    assert connector._enable_iam_auth is False
    await connector.close()


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
async def test_AsyncConnector_init_ip_type(
    ip_type: Union[str, IPTypes], expected: IPTypes, credentials: FakeCredentials
) -> None:
    """
    Test to check whether the __init__ method of AsyncConnector
    properly sets ip_type.
    """
    connector = AsyncConnector(credentials=credentials, ip_type=ip_type)
    assert connector._ip_type == expected
    connector.close()


async def test_AsyncConnector_init_bad_ip_type(credentials: FakeCredentials) -> None:
    """Test that AsyncConnector errors due to bad ip_type str."""
    bad_ip_type = "BAD-IP-TYPE"
    with pytest.raises(ValueError) as exc_info:
        AsyncConnector(ip_type=bad_ip_type, credentials=credentials)
    assert (
        exc_info.value.args[0]
        == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE', 'PSC'."
    )


@pytest.mark.asyncio
async def test_AsyncConnector_context_manager(
    credentials: FakeCredentials,
) -> None:
    """
    Test to check whether the __init__ method of AsyncConnector
    properly sets defaults as context manager.
    """
    async with AsyncConnector(credentials) as connector:
        assert connector._quota_project is None
        assert connector._alloydb_api_endpoint == ALLOYDB_API_ENDPOINT
        assert connector._client is None
        assert connector._credentials == credentials
        assert connector._enable_iam_auth is False


TEST_INSTANCE_NAME = "/".join(
    [
        "projects",
        "PROJECT",
        "locations",
        "REGION",
        "clusters",
        "CLUSTER_NAME",
        "instances",
        "INSTANCE_NAME",
    ],
)


@pytest.mark.asyncio
async def test_connect_and_close(credentials: FakeCredentials) -> None:
    """
    Test that connector.connect calls asyncpg.connect and cleans up
    """
    with patch("google.cloud.alloydb.connector.asyncpg.connect") as connect:
        # patch db connection creation and return plain future
        future = asyncio.Future()
        future.set_result(True)
        connect.return_value = future

        connector = AsyncConnector(credentials)
        connector._client = FakeAlloyDBClient()
        connection = await connector.connect(
            TEST_INSTANCE_NAME,
            "asyncpg",
            user="test-user",
            password="test-password",
            db="test-db",
        )
        await connector.close()

        # check connection is returned
        assert connection.result() is True
        # outside of context manager check close cleaned up
        assert connector._client.closed is True


@pytest.mark.asyncio
async def test_force_refresh(credentials: FakeCredentials) -> None:
    """
    Test that any failed connection results in a force refresh.
    """
    with patch(
        "google.cloud.alloydb.connector.asyncpg.connect",
        side_effect=Exception("connection failed"),
    ):
        connector = AsyncConnector(credentials)
        connector._client = FakeAlloyDBClient()

        # Prepare cached connection info to avoid the need for two calls
        fake = FakeConnectionInfo()
        connector._instances[TEST_INSTANCE_NAME] = fake

        with pytest.raises(Exception):
            await connector.connect(
                TEST_INSTANCE_NAME,
                "asyncpg",
                user="test-user",
                password="test-password",
                db="test-db",
            )

        assert fake._force_refresh_called is True


@pytest.mark.asyncio
async def test_close_stops_instance(credentials: FakeCredentials) -> None:
    """
    Test that any connected instances are closed when the connector is
    closed.
    """
    connector = AsyncConnector(credentials)
    connector._client = FakeAlloyDBClient()
    # Simulate connection
    fake = FakeConnectionInfo()
    connector._instances[TEST_INSTANCE_NAME] = fake

    await connector.close()

    assert fake._close_called is True


@pytest.mark.asyncio
async def test_context_manager_connect_and_close(
    credentials: FakeCredentials,
) -> None:
    """
    Test that connector.connect calls asyncpg.connect and cleans up using the
    async context manager
    """
    with patch("google.cloud.alloydb.connector.asyncpg.connect") as connect:
        fake_client = FakeAlloyDBClient()
        async with AsyncConnector(credentials) as connector:
            connector._client = fake_client

            # patch db connection creation
            future = asyncio.Future()
            future.set_result(True)
            connect.return_value = future

            connection = await connector.connect(
                TEST_INSTANCE_NAME,
                "asyncpg",
                user="test-user",
                password="test-password",
                db="test-db",
            )

            # check connection is returned
            assert connection.result() is True
        # outside of context manager check close cleaned up
        assert fake_client.closed is True


@pytest.mark.asyncio
async def test_connect_unsupported_driver(
    credentials: FakeCredentials,
) -> None:
    """
    Test that connector.connect errors with unsupported database driver.
    """
    client = FakeAlloyDBClient()
    async with AsyncConnector(credentials) as connector:
        connector._client = client
        # try to connect using unsupported driver, should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            await connector.connect(TEST_INSTANCE_NAME, "bad_driver")
        # assert custom error message for unsupported driver is present
        assert (
            exc_info.value.args[0]
            == "Driver 'bad_driver' is not a supported database driver."
        )


def test_synchronous_init(credentials: FakeCredentials) -> None:
    """
    Test that AsyncConnector can be successfully initialized without an
    event loop running.
    """
    connector = AsyncConnector(credentials)
    assert connector._keys is None


async def test_async_connect_bad_ip_type(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """Test that AyncConnector.connect errors due to bad ip_type str."""
    async with AsyncConnector(credentials=credentials) as connector:
        connector._client = fake_client
        bad_ip_type = "BAD-IP-TYPE"
        with pytest.raises(ValueError) as exc_info:
            await connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "asyncpg",
                user="test-user",
                password="test-password",
                db="test-db",
                ip_type=bad_ip_type,
            )
        assert (
            exc_info.value.args[0]
            == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE', 'PSC'."
        )
