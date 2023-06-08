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
from functools import partial
from threading import Thread
from types import TracebackType
from typing import Any, Dict, Optional, Type, TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric import rsa

from google.auth import default
from google.auth.credentials import with_scopes_if_required
from google.cloud.alloydb.connector.client import AlloyDBClient
from google.cloud.alloydb.connector.instance import Instance
import google.cloud.alloydb.connector.pg8000 as pg8000

if TYPE_CHECKING:
    from google.auth.credentials import Credentials


class Connector:
    """A class to configure and create connections to Cloud SQL instances.

    Args:
        credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            If not specified, Application Default Credentials are used.
        quota_project (str): The Project ID for an existing Google Cloud
            project. The project specified is used for quota and
            billing purposes.
            Defaults to None, picking up project from environment.
        alloydb_api_endpoint (str): Base URL to use when calling
            the AlloyDB API endpoint. Defaults to "https://alloydb.googleapis.com".
    """

    def __init__(
        self,
        credentials: Optional[Credentials] = None,
        quota_project: Optional[str] = None,
        alloydb_api_endpoint: str = "https://alloydb.googleapis.com",
    ) -> None:
        # create event loop and start it in background thread
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._instances: Dict[str, Instance] = {}
        # initialize default params
        self._quota_project = quota_project
        self._alloydb_api_endpoint = alloydb_api_endpoint
        # initialize credentials
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if credentials:
            self._credentials = with_scopes_if_required(credentials, scopes=scopes)
        # otherwise use application default credentials
        else:
            self._credentials, _ = default(scopes=scopes)
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._client: Optional[AlloyDBClient] = None

    def connect(self, instance_uri: str, driver: str, **kwargs: Any) -> Any:
        """
        Prepares and returns a database DBAPI connection object.

        Starts background tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect with.
                Supported drivers are pg8000.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        # call async connect and wait on result
        connect_task = asyncio.run_coroutine_threadsafe(
            self.connect_async(instance_uri, driver, **kwargs), self._loop
        )
        return connect_task.result()

    async def connect_async(self, instance_uri: str, driver: str, **kwargs: Any) -> Any:
        """
        Asynchronously prepares and returns a database connection object.

        Starts tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect with.
                Supported drivers are pg8000.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        if self._client is None:
            # lazy init client as it has to be initialized in async context
            self._client = AlloyDBClient(
                self._alloydb_api_endpoint, self._quota_project, self._credentials
            )
        # use existing connection info if possible
        if instance_uri in self._instances:
            instance = self._instances[instance_uri]
        else:
            instance = Instance(
                instance_uri,
                self._client,
                self._key,
            )
            self._instances[instance_uri] = instance

        connect_func = {
            "pg8000": pg8000.connect,
        }
        # only accept supported database drivers
        try:
            connector = connect_func[driver]
        except KeyError:
            raise ValueError(f"Driver '{driver}' is not a supported database driver.")

        # Host and ssl options come from the certificates and instance IP address
        # so we don't want the user to specify them.
        kwargs.pop("host", None)
        kwargs.pop("ssl", None)
        kwargs.pop("port", None)

        # get connection info for AlloyDB instance
        ip_address, context = await instance.connection_info()

        # synchronous drivers are blocking and run using executor
        try:
            connect_partial = partial(connector, ip_address, context, **kwargs)
            return await self._loop.run_in_executor(None, connect_partial)
        except Exception:
            # we attempt a force refresh, then throw the error
            instance.force_refresh()
            raise

    def __enter__(self) -> "Connector":
        """Enter context manager by returning Connector object"""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Exit context manager by closing Connector"""
        self.close()

    def close(self) -> None:
        """Close Connector by stopping tasks and releasing resources."""
        close_future = asyncio.run_coroutine_threadsafe(
            self.close_async(), loop=self._loop
        )
        # Will attempt to gracefully shut down tasks for 3s
        close_future.result(timeout=3)
        # if background thread exists for Connector, clean it up
        if self._thread:
            # stop event loop running in background thread
            self._loop.call_soon_threadsafe(self._loop.stop)
            # wait for thread to finish closing (i.e. loop to stop)
            self._thread.join()

    async def close_async(self) -> None:
        """Helper function to cancel Instances' tasks
        and close client."""
        await asyncio.gather(
            *[instance.close() for instance in self._instances.values()]
        )
        if self._client:
            await self._client.close()
