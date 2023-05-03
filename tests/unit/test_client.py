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

import json
from typing import Any

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric import rsa
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector.client import AlloyDBClient
from google.cloud.alloydb.connector.version import __version__ as version


async def connectionInfo(request: Any) -> web.Response:
    response = {
        "ipAddress": "127.0.0.1",
        "instanceUid": "123456789",
    }
    return web.Response(content_type="application/json", body=json.dumps(response))


async def generateClientCertificate(request: Any) -> web.Response:
    response = {
        "pemCertificate": "This is the client cert",
        "pemCertificateChain": [
            "This is the intermediate cert",
            "This is the root cert",
        ],
    }
    return web.Response(content_type="application/json", body=json.dumps(response))


@pytest.fixture
async def client(aiohttp_client: Any) -> Any:
    app = web.Application()
    metadata_uri = "/v1beta/projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance/connectionInfo"
    app.router.add_get(metadata_uri, connectionInfo)
    client_cert_uri = "/v1beta/projects/test-project/locations/test-region/clusters/test-cluster:generateClientCertificate"
    app.router.add_post(client_cert_uri, generateClientCertificate)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test__get_metadata(client: Any, credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully.
    """
    test_client = AlloyDBClient("", "", credentials, client)
    ip_address = await test_client._get_metadata(
        "test-project",
        "test-region",
        "test-cluster",
        "test-instance",
    )
    assert ip_address == "127.0.0.1"


@pytest.mark.asyncio
async def test__get_client_certificate(
    client: Any, credentials: FakeCredentials
) -> None:
    """
    Test _get_client_certificate returns successfully.
    """
    test_client = AlloyDBClient("", "", credentials, client)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certs = await test_client._get_client_certificate(
        "test-project", "test-region", "test-cluster", key
    )
    client_cert, cert_chain = certs
    assert client_cert == "This is the client cert"
    assert cert_chain[0] == "This is the intermediate cert"
    assert cert_chain[1] == "This is the root cert"


@pytest.mark.asyncio
async def test_AlloyDBClient_init_(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of AlloyDBClient
    can correctly initialize a client.
    """
    client = AlloyDBClient("www.test-endpoint.com", "my-quota-project", credentials)
    # verify base endpoint is set
    assert client._alloydb_api_endpoint == "www.test-endpoint.com"
    # verify proper headers are set
    assert client._client.headers["User-Agent"] == f"alloydb-python-connector/{version}"
    assert client._client.headers["x-goog-user-project"] == "my-quota-project"
