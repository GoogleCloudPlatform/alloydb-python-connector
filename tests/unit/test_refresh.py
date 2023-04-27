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
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector.refresh import (
    _get_client_certificate,
    _get_metadata,
)


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
            "This is the interediate cert",
            "This is the root cert",
        ],
    }
    return web.Response(content_type="application/json", body=json.dumps(response))


@pytest.mark.asyncio
async def test__get_metadata(aiohttp_client: Any, credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully.
    """
    app = web.Application()
    api_version = "v1beta"
    project = "test-project"
    region = "test-region"
    cluster = "test-cluster"
    name = "test-instance"
    uri = f"/{api_version}/projects/{project}/locations/{region}/clusters/{cluster}/instances/{name}/connectionInfo"
    app.router.add_get(uri, connectionInfo)
    client = await aiohttp_client(app)
    metadata = await _get_metadata(
        client, "", credentials, project, region, cluster, name
    )
    assert metadata["ip_address"] == "127.0.0.1"
    assert metadata["uid"] == "123456789"


@pytest.mark.asyncio
async def test__get_client_certificate(
    aiohttp_client: Any, credentials: FakeCredentials
) -> None:
    """
    Test _get_client_certificate returns successfully.
    """
    app = web.Application()
    api_version = "v1beta"
    project = "test-project"
    region = "test-region"
    cluster = "test-cluster"
    key = rsa.generate_private_key(
        backend=default_backend(), public_exponent=65537, key_size=2048
    )
    uri = f"/{api_version}/projects/{project}/locations/{region}/clusters/{cluster}:generateClientCertificate"
    app.router.add_post(uri, generateClientCertificate)
    client = await aiohttp_client(app)
    certs = await _get_client_certificate(
        client, "", credentials, project, region, cluster, key
    )
    assert certs["client_cert"] == "This is the client cert"
    assert certs["intermediate_cert"] == "This is the interediate cert"
    assert certs["root_cert"] == "This is the root cert"
