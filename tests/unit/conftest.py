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
from threading import Thread
from typing import Final

from aiofiles.tempfile import TemporaryDirectory
from mocks import FakeAlloyDBClient
from mocks import FakeCredentials
from mocks import FakeInstance
from mocks import metadata_exchange
import pytest

from google.cloud.alloydb.connector.utils import _write_to_file

DELAY: Final[float] = 1.0


@pytest.fixture
def credentials() -> FakeCredentials:
    return FakeCredentials()


@pytest.fixture(scope="session")
def fake_instance() -> FakeInstance:
    return FakeInstance()


@pytest.fixture
def fake_client(fake_instance: FakeInstance) -> FakeAlloyDBClient:
    return FakeAlloyDBClient(fake_instance)


async def start_proxy_server(instance: FakeInstance) -> None:
    """Run local proxy server capable of performing metadata exchange"""
    ip_address = "127.0.0.1"
    port = 5433
    # create socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # create SSL/TLS context
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        root, _, server = instance.get_pem_certs()
        # tmpdir and its contents are automatically deleted after the CA cert
        # and cert chain are loaded into the SSLcontext. The values
        # need to be written to files in order to be loaded by the SSLContext
        async with TemporaryDirectory() as tmpdir:
            _, cert_chain_filename, key_filename = await _write_to_file(
                tmpdir, server, [server, root], instance.server_key
            )
            context.load_cert_chain(cert_chain_filename, key_filename)
        # bind socket to AlloyDB proxy server port on localhost
        sock.bind((ip_address, port))
        # listen for incoming connections
        sock.listen(5)

        with context.wrap_socket(sock, server_side=True) as ssock:
            while True:
                conn, _ = ssock.accept()
                metadata_exchange(conn)
                conn.sendall(instance.name.encode("utf-8"))
                conn.close()


@pytest.fixture(scope="session")
def proxy_server(fake_instance: FakeInstance) -> None:
    """Run local proxy server capable of performing metadata exchange"""
    thread = Thread(
        target=asyncio.run,
        args=(
            start_proxy_server(
                fake_instance,
            ),
        ),
        daemon=True,
    )
    thread.start()
    thread.join(DELAY)  # add a delay to allow the proxy server to start
