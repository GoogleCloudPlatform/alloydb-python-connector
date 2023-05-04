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
import logging
import ssl
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric import rsa

from google.cloud.alloydb.connector.client import AlloyDBClient
from google.cloud.alloydb.connector.exceptions import RefreshError
from google.cloud.alloydb.connector.rate_limiter import AsyncRateLimiter
from google.cloud.alloydb.connector.refresh import (
    _is_valid,
    _seconds_until_refresh,
    RefreshResult,
)

logger = logging.getLogger(name=__name__)


class Instance:
    """
    Manages the information used to connect to the AlloyDB instance.

    Periodically calls the AlloyDB Admin API, automatically refreshing the
    required information approximately 4 minutes before the previous
    certificate expires (every ~56 minutes).

    Args:
        instance_uri (str): The instance URI of the AlloyDB instance.
            ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
        client (AlloyDBClient): Client used to make requests to AlloyDB Admin APIs.
        key (rsa.RSAPrivateKey): Client private key used in refresh operation
            to generate client certificate.
    """

    def __init__(
        self, instance_uri: str, client: AlloyDBClient, key: rsa.RSAPrivateKey
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
        self._current: asyncio.Task = self._schedule_refresh(0)
        self._next: asyncio.Task = self._current

    async def _perform_refresh(self) -> RefreshResult:
        """
        Perform a refresh operation on an AlloyDB instance.

        Retrieves metadata and generates new client certificate
        required to connect securely to the AlloyDB instance.

        Returns:
            RefreshResult: Result of the refresh operation.
        """
        self._refresh_in_progress.set()
        logger.debug(f"['{self._instance_uri}']: Entered _perform_refresh")

        try:
            await self._refresh_rate_limiter.acquire()

            # fetch metadata
            metadata_task = asyncio.create_task(
                self._client._get_metadata(
                    self._project,
                    self._region,
                    self._cluster,
                    self._name,
                )
            )
            # generate client and CA certs
            certs_task = asyncio.create_task(
                self._client._get_client_certificate(
                    self._project,
                    self._region,
                    self._cluster,
                    self._key,
                )
            )

            ip_addr, certs = await asyncio.gather(metadata_task, certs_task)

        except Exception:
            logger.debug(
                f"['{self._instance_uri}']: Error occurred during _perform_refresh."
            )
            raise

        finally:
            self._refresh_in_progress.clear()

        return RefreshResult(ip_addr, self._key, certs)

    def _schedule_refresh(self, delay: int) -> asyncio.Task:
        """
        Schedule a refresh operation.

        Args:
            delay (int): Time in seconds to sleep before performing refresh.

        Returns:
            asyncio.Task[RefreshResult]: A task representing the scheduled
                refresh operation.
        """
        return asyncio.create_task(self._refresh_operation(delay))

    async def _refresh_operation(self, delay: int) -> RefreshResult:
        """
        A coroutine that sleeps for the specified amount of time before
        running _perform_refresh.

        Args:
            delay (int): Time in seconds to sleep before performing refresh.

        Returns:
            RefreshResult: Refresh result for an AlloyDB instance.
        """
        refresh_task: asyncio.Task
        try:
            if delay > 0:
                logger.debug(f"['{self._instance_uri}']: Entering sleep")
                await asyncio.sleep(delay)
            refresh_task = asyncio.create_task(self._perform_refresh())
            refresh_result = await refresh_task
            # check that refresh is valid
            if not await _is_valid(refresh_task):
                raise RefreshError(
                    f"['{self._instance_uri}']: Invalid refresh operation. Certficate appears to be expired."
                )
        # bad refresh attempt
        except Exception:
            logger.info(
                f"['{self._instance_uri}']: "
                "An error occurred while performing refresh. "
                "Scheduling another refresh attempt immediately"
            )
            # check if current refresh result is invalid (expired),
            # don't want to replace valid result with invalid refresh
            if not await _is_valid(self._current):
                self._current = refresh_task
            # schedule new refresh attempt immediately
            self._next = self._schedule_refresh(0)
            raise
        # if valid refresh, replace current with valid refresh result and schedule next refresh
        self._current = refresh_task
        # calculate refresh delay based on certificate expiration
        delay = _seconds_until_refresh(refresh_result.expiration)
        self._next = self._schedule_refresh(delay)

        return refresh_result

    def force_refresh(self) -> None:
        """
        Schedules a new refresh operation immediately to be used
        for future connection attempts.
        """
        # if next refresh is not already in progress, cancel it and schedule new one immediately
        if not self._refresh_in_progress.is_set():
            self._next.cancel()
            self._next = self._schedule_refresh(0)
        # block all sequential connection attempts on the next refresh result
        self._current = self._next

    async def connection_info(self) -> Tuple[str, ssl.SSLContext]:
        """
        Return connection info for current refresh result.

        Returns:
            Tuple[str, ssl.SSLContext]: AlloyDB instance IP address
                and configured TLS connection.
        """
        refresh: RefreshResult = await self._current
        return refresh.instance_ip, refresh.context

    async def close(self) -> None:
        """
        Cancel refresh tasks.
        """
        logger.debug(f"['{self._instance_uri}']: Waiting for _current to be cancelled")
        self._current.cancel()
        logger.debug(f"['{self._instance_uri}']: Waiting for _next to be cancelled")
        self._next.cancel()
        # gracefully wait for tasks to cancel
        tasks = asyncio.gather(self._current, self._next, return_exceptions=True)
        await asyncio.wait_for(tasks, timeout=2.0)
