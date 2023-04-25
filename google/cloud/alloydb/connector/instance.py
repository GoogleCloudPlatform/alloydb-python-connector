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

import aiohttp
from cryptography.hazmat.primitives.asymmetric import rsa

from google.cloud.alloydb.connector.rate_limiter import AsyncRateLimiter


class Instance:
    """
    Manages the information used to connect to the AlloyDB instance.

    Periodically calls the AlloyDB Admin API, automatically refreshing the
    required information approximately 4 minutes before the previous
    certificate expires (every ~56 minutes).

    Args:
        instance_uri (str): The instance URI of the AlloyDB instance.
            ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>

        client (aiohttp.ClientSession): Async client used to make requests to
            AlloyDB Admin APIs.

        key (rsa.RSAPrivateKey): Client private key used in refresh operation
            to generate client certificate.
    """

    def __init__(
        self, instance_uri: str, client: aiohttp.ClientSession, key: rsa.RSAPrivateKey
    ) -> None:
        # validate and parse instance_uri
        instance_uri_split = instance_uri.split("/")
        # should take form "projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>"
        if len(instance_uri_split) == 8:
            self._instance_uri = instance_uri
            self._project = instance_uri_split[1]
            self._region = instance_uri_split[3]
            self._cluster = instance_uri_split[5]
            self._name = instance_uri_split[7]
        else:
            raise ValueError(
                "Arg `instance_uri` must have "
                "format: projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>, "
                f"got {instance_uri}."
            )

        self._client = client
        self._key = key
        self._refresh_rate_limiter = AsyncRateLimiter(
            max_capacity=2,
            rate=1 / 30,
        )
        self._refresh_in_progress = asyncio.locks.Event()
        # For the initial refresh operation, set current = next so that
        # connection requests block until the first refresh is complete.
        self._current = self._schedule_refresh(0)
        self._next = self._current

    def _schedule_refresh(self, sleep: int) -> None:
        return NotImplementedError
