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

from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector.refresh import (
    _get_client_certificate,
    _get_metadata,
)


@pytest.mark.asyncio
async def test__get_metadata(client: Any, credentials: FakeCredentials) -> None:
    """
    Test _get_metadata returns successfully.
    """
    metadata = await _get_metadata(
        client,
        "",
        credentials,
        "test-project",
        "test-region",
        "test-cluster",
        "test-instance",
    )
    assert metadata["ip_address"] == "127.0.0.1"
    assert metadata["uid"] == "123456789"


@pytest.mark.asyncio
async def test__get_client_certificate(
    client: Any, credentials: FakeCredentials
) -> None:
    """
    Test _get_client_certificate returns successfully.
    """
    key = rsa.generate_private_key(
        backend=default_backend(), public_exponent=65537, key_size=2048
    )
    certs = await _get_client_certificate(
        client, "", credentials, "test-project", "test-region", "test-cluster", key
    )
    assert certs["client_cert"] == "This is the client cert"
    assert certs["intermediate_cert"] == "This is the intermediate cert"
    assert certs["root_cert"] == "This is the root cert"
