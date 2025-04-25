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

from typing import Optional

import google.cloud.alloydb_v1beta as v1beta
from mocks import FakeAlloyDBAdminAsyncClient
from mocks import FakeAlloyDBAdminClient
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector.client import AlloyDBClient
from google.cloud.alloydb.connector.utils import generate_keys
from google.cloud.alloydb.connector.version import __version__ as version


@pytest.mark.asyncio
async def test__get_metadata(credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    ip_addrs = await test_client._get_metadata(
        "test-project",
        "test-region",
        "test-cluster",
        "test-instance",
    )
    assert ip_addrs == {
        "PRIVATE": "10.0.0.1",
        "PUBLIC": "",
        "PSC": "",
    }


@pytest.mark.asyncio
async def test__get_metadata_with_public_ip(credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully with Public IP.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    ip_addrs = await test_client._get_metadata(
        "test-project",
        "test-region",
        "test-cluster",
        "public-instance",
    )
    assert ip_addrs == {
        "PRIVATE": "10.0.0.1",
        "PUBLIC": "127.0.0.1",
        "PSC": "",
    }


@pytest.mark.asyncio
async def test__get_metadata_with_psc(credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully with PSC DNS name.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    ip_addrs = await test_client._get_metadata(
        "test-project",
        "test-region",
        "test-cluster",
        "psc-instance",
    )
    assert ip_addrs == {
        "PRIVATE": "",
        "PUBLIC": "",
        "PSC": "x.y.alloydb.goog",
    }


async def test__get_metadata_with_async_client(credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully for an async client.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    test_client._is_sync = False
    assert (
        await test_client._get_metadata(
            "test-project",
            "test-region",
            "test-cluster",
            "psc-instance",
        )
        is not None
    )


async def test__get_metadata_with_sync_client(credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully for a sync client.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminClient())
    test_client._is_sync = True
    assert (
        await test_client._get_metadata(
            "test-project",
            "test-region",
            "test-cluster",
            "psc-instance",
        )
        is not None
    )


@pytest.mark.asyncio
async def test__get_client_certificate(credentials: FakeCredentials) -> None:
    """
    Test _get_client_certificate returns successfully.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    keys = await generate_keys()
    certs = await test_client._get_client_certificate(
        "test-project", "test-region", "test-cluster", keys[1]
    )
    ca_cert, cert_chain = certs
    assert ca_cert == "This is the CA cert"
    assert cert_chain[0] == "This is the client cert"
    assert cert_chain[1] == "This is the intermediate cert"
    assert cert_chain[2] == "This is the root cert"


async def test__get_client_certificate_with_async_client(
    credentials: FakeCredentials,
) -> None:
    """
    Test _get_client_certificate returns successfully for an async client.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminAsyncClient())
    test_client._is_sync = False
    keys = await generate_keys()
    assert (
        await test_client._get_client_certificate(
            "test-project", "test-region", "test-cluster", keys[1]
        )
        is not None
    )


async def test__get_client_certificate_with_sync_client(
    credentials: FakeCredentials,
) -> None:
    """
    Test _get_client_certificate returns successfully for a sync client.
    """
    test_client = AlloyDBClient("", "", credentials, FakeAlloyDBAdminClient())
    test_client._is_sync = True
    keys = await generate_keys()
    assert (
        await test_client._get_client_certificate(
            "test-project", "test-region", "test-cluster", keys[1]
        )
        is not None
    )


@pytest.mark.asyncio
async def test_AlloyDBClient_init_(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of AlloyDBClient
    can correctly initialize a client.
    """
    client = AlloyDBClient("www.test-endpoint.com", "my-quota-project", credentials)
    # verify base endpoint is set
    assert client._client.api_endpoint == "www.test-endpoint.com"
    # verify proper headers are set
    assert client._user_agent.startswith(f"alloydb-python-connector/{version}")
    assert client._client._client._client_options.quota_project_id == "my-quota-project"


@pytest.mark.asyncio
async def test_AlloyDBClient_init_custom_user_agent(
    credentials: FakeCredentials,
) -> None:
    """
    Test to check that custom user agents are included in HTTP requests.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com",
        "my-quota-project",
        credentials,
        user_agent="custom-agent/v1.0.0 other-agent/v2.0.0",
    )
    assert client._user_agent.startswith(
        f"alloydb-python-connector/{version} custom-agent/v1.0.0 other-agent/v2.0.0"
    )


async def test_AlloyDBClient_init_specified_client(
    credentials: FakeCredentials,
) -> None:
    """
    Test to check that __init__ method of AlloyDBClient uses specified client.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com",
        "my-quota-project",
        credentials,
        FakeAlloyDBAdminAsyncClient(),
    )
    assert client._is_sync is False
    assert type(client._client) is FakeAlloyDBAdminAsyncClient


async def test_AlloyDBClient_init_sync_client(credentials: FakeCredentials) -> None:
    """
    Test to check that __init__ method of AlloyDBClient creates a sync client
    when client is not specified and driver is pg8000.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com", "my-quota-project", credentials, driver="pg8000"
    )
    assert client._is_sync is True
    assert type(client._client) is v1beta.AlloyDBAdminClient
    assert client._client.transport.kind == "grpc"


async def test_AlloyDBClient_init_async_client(credentials: FakeCredentials) -> None:
    """
    Test to check that __init__ method of AlloyDBClient creates an async client
    when client is not specified and driver is not pg8000.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com", "my-quota-project", credentials, driver=""
    )
    assert client._is_sync is False
    assert type(client._client) is v1beta.AlloyDBAdminAsyncClient
    assert client._client.transport.kind == "grpc_asyncio"


@pytest.mark.parametrize(
    "driver",
    [None, "pg8000", "asyncpg"],
)
@pytest.mark.asyncio
async def test_AlloyDBClient_user_agent(
    driver: Optional[str], credentials: FakeCredentials
) -> None:
    """
    Test to check whether the __init__ method of AlloyDBClient
    properly sets user agent when passed a database driver.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com", "my-quota-project", credentials, driver=driver
    )
    if driver is None:
        assert client._user_agent.startswith(f"alloydb-python-connector/{version}")
    else:
        assert client._user_agent.startswith(
            f"alloydb-python-connector/{version}+{driver}"
        )


@pytest.mark.parametrize(
    "driver, expected",
    [(None, False), ("pg8000", True), ("asyncpg", False)],
)
@pytest.mark.asyncio
async def test_AlloyDBClient_use_metadata(
    driver: Optional[str], expected: bool, credentials: FakeCredentials
) -> None:
    """
    Test to check whether the __init__ method of AlloyDBClient
    properly sets use_metadata.
    """
    client = AlloyDBClient(
        "www.test-endpoint.com", "my-quota-project", credentials, driver=driver
    )
    assert client._use_metadata == expected
