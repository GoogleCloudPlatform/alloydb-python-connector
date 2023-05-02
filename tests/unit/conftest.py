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

from threading import Thread
from typing import Any, Generator

from aiohttp import web
from mocks import (
    connectionInfo,
    FakeCredentials,
    FakeInstance,
    generateClientCertificate,
)
import pytest


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


@pytest.fixture
def fake_instance() -> Generator:
    instance = FakeInstance()
    # generate certs for fake AlloyDB instance
    instance.generate_certs()
    # start test server
    instance.configure_tls()
    thread = Thread(target=instance.start_server_proxy, daemon=True)
    thread.start()
    yield instance
    # close server
    instance.server.close()
