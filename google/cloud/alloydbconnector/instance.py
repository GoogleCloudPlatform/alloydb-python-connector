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

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import logging
import re
from typing import TYPE_CHECKING

from google.cloud.alloydbconnector.connection_info import ConnectionInfo
from google.cloud.alloydbconnector.exceptions import RefreshError
from google.cloud.alloydbconnector.rate_limiter import AsyncRateLimiter
from google.cloud.alloydbconnector.refresh_utils import _is_valid
from google.cloud.alloydbconnector.refresh_utils import _seconds_until_refresh

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric import rsa

    from google.cloud.alloydbconnector.client import AlloyDBClient

logger = logging.getLogger(name=__name__)

INSTANCE_URI_REGEX = re.compile(
    "projects/([^:]+(:[^:]+)?)/locations/([^:]+)/clusters/([^:]+)/instances/([^:]+)"
)


def _parse_instance_uri(instance_uri: str) -> tuple[str, str, str, str]:
    # should take form "projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>"
    if INSTANCE_URI_REGEX.fullmatch(instance_uri) is None:
        raise ValueError(
            "Arg `instance_uri` must have "
            "format: projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>, projects/<DOMAIN>:<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>"
            f"got {instance_uri}."
        )
    instance_uri_split = INSTANCE_URI_REGEX.split(instance_uri)
    return (
        instance_uri_split[1],
        instance_uri_split[3],
        instance_uri_split[4],
        instance_uri_split[5],
    )


class RefreshAheadCache:
    """
    Manages the information used to connect to the AlloyDB instance.

    Periodically calls the AlloyDB API, automatically refreshing the
    required information approximately 4 minutes before the previous
    certificate expires (every ~56 minutes).

    Args:
        instance_uri (str): The instance URI of the AlloyDB instance.
            ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
        client (AlloyDBClient): Client used to make requests to AlloyDB APIs.
        keys (tuple[rsa.RSAPrivateKey, str]): Private and Public key pair.
    """

    def __init__(
        self,
        instance_uri: str,
        client: AlloyDBClient,
        keys: asyncio.Future[tuple[rsa.RSAPrivateKey, str]],
    ) -> None:
        # validate and parse instance_uri
        self._project, self._region, self._cluster, self._name = _parse_instance_uri(
            instance_uri
        )

        self._instance_uri = instance_uri
        self._client = client
        self._keys = keys
        self._refresh_rate_limiter = AsyncRateLimiter(
            max_capacity=2,
            rate=1 / 30,
        )
        self._refresh_in_progress = asyncio.locks.Event()
        # For the initial refresh operation, set current = next so that
        # connection requests block until the first refresh is complete.
        self._current: asyncio.Task = self._schedule_refresh(0)
        self._next: asyncio.Task = self._current

    async def _perform_refresh(self) -> ConnectionInfo:
        """
        Perform a refresh operation on an AlloyDB instance.

        Retrieves metadata and generates new client certificate
        required to connect securely to the AlloyDB instance.

        Returns:
            ConnectionInfo: Result of the refresh operation.
        """
        self._refresh_in_progress.set()
        logger.debug(
            f"['{self._instance_uri}']: Connection info refresh operation started"
        )

        try:
            await self._refresh_rate_limiter.acquire()
            connection_info = await self._client.get_connection_info(
                self._project,
                self._region,
                self._cluster,
                self._name,
                self._keys,
            )
            logger.debug(
                f"['{self._instance_uri}']: Connection info refresh operation"
                " complete"
            )
            logger.debug(
                f"['{self._instance_uri}']: Current certificate expiration = "
                f"{connection_info.expiration.isoformat()}"
            )

        except Exception as e:
            logger.debug(
                f"['{self._instance_uri}']: Connection info refresh operation"
                f" failed: {str(e)}"
            )
            raise

        finally:
            self._refresh_in_progress.clear()
        return connection_info

    def _schedule_refresh(self, delay: int) -> asyncio.Task:
        """
        Schedule a refresh operation.

        Args:
            delay (int): Time in seconds to sleep before performing refresh.

        Returns:
            asyncio.Task[ConnectionInfo]: A task representing the scheduled
                refresh operation.
        """
        return asyncio.create_task(self._refresh_operation(delay))

    async def _refresh_operation(self, delay: int) -> ConnectionInfo:
        """
        A coroutine that sleeps for the specified amount of time before
        running _perform_refresh.

        Args:
            delay (int): Time in seconds to sleep before performing refresh.

        Returns:
            ConnectionInfo: Refresh result for an AlloyDB instance.
        """
        refresh_task: asyncio.Task
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            refresh_task = asyncio.create_task(self._perform_refresh())
            refresh_result = await refresh_task
            # check that refresh is valid
            if not await _is_valid(refresh_task):
                raise RefreshError(
                    f"['{self._instance_uri}']: Invalid refresh operation. Certficate appears to be expired."
                )
        except asyncio.CancelledError:
            logger.debug(
                f"['{self._instance_uri}']: Scheduled refresh operation cancelled"
            )
            raise
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
        logger.debug(
            f"['{self._instance_uri}']: Connection info refresh operation"
            " scheduled for "
            f"{(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec='seconds')} "
            f"(now + {timedelta(seconds=delay)})"
        )
        self._next = self._schedule_refresh(delay)

        return refresh_result

    async def force_refresh(self) -> None:
        """
        Schedules a new refresh operation immediately to be used
        for future connection attempts.
        """
        # if next refresh is not already in progress, cancel it and schedule new one immediately
        if not self._refresh_in_progress.is_set():
            self._next.cancel()
            self._next = self._schedule_refresh(0)
        # block all sequential connection attempts on the next refresh result if current is invalid
        if not await _is_valid(self._current):
            self._current = self._next

    async def connect_info(self) -> ConnectionInfo:
        """Retrieves ConnectionInfo instance for establishing a secure
        connection to the AlloyDB instance.
        """
        return await self._current

    async def close(self) -> None:
        """
        Cancel refresh tasks.
        """
        logger.debug(
            f"['{self._instance_uri}']: Canceling connection info refresh"
            " operation tasks"
        )
        self._current.cancel()
        self._next.cancel()
        # gracefully wait for tasks to cancel
        tasks = asyncio.gather(self._current, self._next, return_exceptions=True)
        await asyncio.wait_for(tasks, timeout=2.0)
