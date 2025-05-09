# Copyright 2024 Google LLC
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
import logging
from typing import Optional

from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.connection_info import ConnectionInfo
from google.cloud.alloydbconnector.instance import _parse_instance_uri
from google.cloud.alloydbconnector.refresh_utils import _refresh_buffer

logger = logging.getLogger(name=__name__)


class LazyRefreshCache:
    """Cache that refreshes connection info when a caller requests a connection.

    Only refreshes the cache when a new connection is requested and the current
    certificate is close to or already expired.

    This is the recommended option for serverless environments.
    """

    def __init__(
        self,
        instance_uri: str,
        client: AlloyDBClient,
        keys: asyncio.Future,
    ) -> None:
        """Initializes a LazyRefreshCache instance.

        Args:
            instance_connection_string (str): The AlloyDB Instance's
                connection URI.
            client (AlloyDBClient): The AlloyDB client instance.
            keys (asyncio.Future): A future to the client's public-private key
                pair.
        """
        # validate and parse instance connection name
        self._project, self._region, self._cluster, self._name = _parse_instance_uri(
            instance_uri
        )
        self._instance_uri = instance_uri

        self._keys = keys
        self._client = client
        self._lock = asyncio.Lock()
        self._cached: Optional[ConnectionInfo] = None
        self._needs_refresh = False

    async def force_refresh(self) -> None:
        """
        Invalidates the cache and configures the next call to
        connect_info() to retrieve a fresh ConnectionInfo instance.
        """
        async with self._lock:
            self._needs_refresh = True

    async def connect_info(self) -> ConnectionInfo:
        """Retrieves ConnectionInfo instance for establishing a secure
        connection to the AlloyDB instance.
        """
        async with self._lock:
            # If connection info is cached, check expiration.
            # Pad expiration with a buffer to give the client plenty of time to
            # establish a connection to the server with the certificate.
            if (
                self._cached
                and not self._needs_refresh
                and datetime.now(timezone.utc)
                < (self._cached.expiration - timedelta(seconds=_refresh_buffer))
            ):
                logger.debug(
                    f"['{self._instance_uri}']: Connection info "
                    "is still valid, using cached info"
                )
                return self._cached
            logger.debug(
                f"['{self._instance_uri}']: Connection info "
                "refresh operation started"
            )
            try:
                conn_info = await self._client.get_connection_info(
                    self._project,
                    self._region,
                    self._cluster,
                    self._name,
                    self._keys,
                )
            except Exception as e:
                logger.debug(
                    f"['{self._instance_uri}']: Connection info "
                    f"refresh operation failed: {str(e)}"
                )
                raise
            logger.debug(
                f"['{self._instance_uri}']: Connection info "
                "refresh operation completed successfully"
            )
            logger.debug(
                f"['{self._instance_uri}']: Current certificate "
                f"expiration = {str(conn_info.expiration)}"
            )
            self._cached = conn_info
            self._needs_refresh = False
            return conn_info

    async def close(self) -> None:
        """Close is a no-op and provided purely for a consistent interface with
        other cache types.
        """
        pass
