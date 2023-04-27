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
from mocks import FakeCredentials
import pytest


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
def credentials() -> FakeCredentials:
    return FakeCredentials()


@pytest.fixture
async def client(aiohttp_client: Any) -> Any:
    app = web.Application()
    metadata_uri = "/v1beta/projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance/connectionInfo"
    app.router.add_get(metadata_uri, connectionInfo)
    client_cert_uri = "/v1beta/projects/test-project/locations/test-region/clusters/test-cluster:generateClientCertificate"
    app.router.add_post(client_cert_uri, generateClientCertificate)
    return await aiohttp_client(app)
