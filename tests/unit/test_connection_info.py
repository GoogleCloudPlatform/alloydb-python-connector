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
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mocks import FakeAlloyDBClient
from mocks import FakeInstance
import pytest

from google.cloud.alloydb.connector.connection_info import ConnectionInfo
from google.cloud.alloydb.connector.exceptions import IPTypeNotFoundError
from google.cloud.alloydb.connector.instance import IPTypes
from google.cloud.alloydb.connector.instance import RefreshAheadCache
from google.cloud.alloydb.connector.utils import generate_keys


def test_ConnectionInfo_init_(fake_instance: FakeInstance) -> None:
    """
    Test to check whether the __init__ method of ConnectionInfo
    can correctly initialize TLS context.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_cert, intermediate_cert, ca_cert = fake_instance.get_pem_certs()
    # build client cert
    client_cert = (
        x509.CertificateBuilder()
        .subject_name(fake_instance.intermediate_cert.subject)
        .issuer_name(fake_instance.intermediate_cert.issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(minutes=10))
    )
    # sign client cert with intermediate cert
    client_cert = client_cert.sign(fake_instance.intermediate_key, hashes.SHA256())
    client_cert = client_cert.public_bytes(encoding=serialization.Encoding.PEM).decode(
        "UTF-8"
    )
    conn_info = ConnectionInfo(
        [client_cert, intermediate_cert, root_cert],
        ca_cert,
        key,
        fake_instance.ip_addrs,
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    context = conn_info.create_ssl_context()
    # verify TLS requirements
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3


def test_ConnectionInfo_caches_sslcontext() -> None:
    info = ConnectionInfo(["cert"], "cert", "key".encode(), {}, datetime.now())
    # context should default to None
    assert info.context is None
    # cache a 'context'
    info.context = "context"
    # caling create_ssl_context should no-op with an existing 'context'
    info.create_ssl_context()
    assert info.context == "context"


@pytest.mark.parametrize(
    "ip_type, expected",
    [
        (
            IPTypes.PRIVATE,
            "127.0.0.1",
        ),
        (
            IPTypes.PUBLIC,
            "0.0.0.0",
        ),
        (
            IPTypes.PSC,
            "x.y.alloydb.goog",
        ),
    ],
)
async def test_ConnectionInfo_get_preferred_ip(ip_type: IPTypes, expected: str) -> None:
    """Test that ConnectionInfo.get_preferred_ip returns proper ip address."""
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    cache = RefreshAheadCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    conn_info = await cache.connect_info()
    ip_address = conn_info.get_preferred_ip(ip_type)
    assert ip_address == expected
    # close instance
    await cache.close()


async def test_ConnectionInfo_get_preferred_ip_IPTypeNotFoundError() -> None:
    """Test that ConnectionInfo.get_preferred_ip throws IPTypeNotFoundError"""
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    # set ip_addrs to have no public IP
    client.instance.ip_addrs = {"PRIVATE": "10.0.0.1"}
    cache = RefreshAheadCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    conn_info = await cache.connect_info()
    # check RefreshError is thrown
    with pytest.raises(IPTypeNotFoundError):
        conn_info.get_preferred_ip(ip_type=IPTypes.PUBLIC)
    # close instance
    await cache.close()
