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

import aiohttp
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from google.cloud.alloydb.connector.instance import Instance


@pytest.mark.asyncio
async def test_Instance_init() -> None:
    """
    Test to check whether the __init__ method of Instance
    can tell if the instance URI that's passed in is formatted correctly.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    async with aiohttp.ClientSession() as client:
        instance = Instance(
            "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
            client,
            key,
        )
        assert (
            instance._project == "test-project"
            and instance._region == "test-region"
            and instance._cluster == "test-cluster"
            and instance._name == "test-instance"
        )


@pytest.mark.asyncio
async def test_Instance_init_invalid_instant_uri() -> None:
    """
    Test to check whether the __init__ method of Instance
    will throw error for invalid instance URI.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    async with aiohttp.ClientSession() as client:
        with pytest.raises(ValueError):
            Instance("invalid/instance/uri/", client, key)
