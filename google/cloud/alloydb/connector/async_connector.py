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

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Dict, Optional, Type, TYPE_CHECKING

import google.auth
from google.auth.credentials import with_scopes_if_required
import google.auth.transport.requests

import google.cloud.alloydb.connector.asyncpg as asyncpg
from google.cloud.alloydb.connector.client import AlloyDBClient
from google.cloud.alloydb.connector.instance import Instance
from google.cloud.alloydb.connector.instance import IPTypes
from google.cloud.alloydb.connector.utils import generate_keys

if TYPE_CHECKING:
    from google.auth.credentials import Credentials


class AsyncConnector:
    """A class to configure and create connections to Cloud SQL instances
    asynchronously.

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
        enable_iam_auth (bool): Enables automatic IAM database authentication.
        ip_type (str | IPTypes): Default IP type for all AlloyDB connections.
            Defaults to IPTypes.PRIVATE ("PRIVATE") for private IP connections.
    """

    def __init__(
        self,
        credentials: Optional[Credentials] = None,
        quota_project: Optional[str] = None,
        alloydb_api_endpoint: str = "https://alloydb.googleapis.com",
        enable_iam_auth: bool = False,
        ip_type: str | IPTypes = IPTypes.PRIVATE,
        user_agent: Optional[str] = None,
    ) -> None:
        self._instances: Dict[str, Instance] = {}
        # initialize default params
        self._quota_project = quota_project
        self._alloydb_api_endpoint = alloydb_api_endpoint
        self._enable_iam_auth = enable_iam_auth
        # if ip_type is str, convert to IPTypes enum
        if isinstance(ip_type, str):
            ip_type = IPTypes(ip_type.upper())
        self._ip_type = ip_type
        self._user_agent = user_agent
        # initialize credentials
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if credentials:
            self._credentials = with_scopes_if_required(credentials, scopes=scopes)
        # otherwise use application default credentials
        else:
            self._credentials, _ = google.auth.default(scopes=scopes)

        # check if AsyncConnector is being initialized with event loop running
        # Otherwise we will lazy init keys
        try:
            self._keys: Optional[asyncio.Task] = asyncio.create_task(generate_keys())
        except RuntimeError:
            self._keys = None
        self._client: Optional[AlloyDBClient] = None

    async def connect(
        self,
        instance_uri: str,
        driver: str,
        **kwargs: Any,
    ) -> Any:
        """
        Asynchronously prepares and returns a database connection object.

        Starts tasks to refresh the certificates and get
        AlloyDB instance IP address. Creates a secure TLS connection
        to establish connection to AlloyDB instance.

        Args:
            instance_uri (str): The instance URI of the AlloyDB instance.
                ex. projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>
            driver (str): A string representing the database driver to connect
                with. Supported drivers are asyncpg.
            **kwargs: Pass in any database driver-specific arguments needed
                to fine tune connection.

        Returns:
            connection: A DBAPI connection to the specified AlloyDB instance.
        """
        if self._keys is None:
            self._keys = asyncio.create_task(generate_keys())
        if self._client is None:
            # lazy init client as it has to be initialized in async context
            self._client = AlloyDBClient(
                self._alloydb_api_endpoint,
                self._quota_project,
                self._credentials,
                user_agent=self._user_agent,
                driver=driver,
            )

        enable_iam_auth = kwargs.pop("enable_iam_auth", self._enable_iam_auth)

        # use existing connection info if possible
        if instance_uri in self._instances:
            instance = self._instances[instance_uri]
        else:
            instance = Instance(instance_uri, self._client, self._keys)
            self._instances[instance_uri] = instance

        connect_func = {
            "asyncpg": asyncpg.connect,
        }
        # only accept supported database drivers
        try:
            connector = connect_func[driver]
        except KeyError:
            raise ValueError(f"Driver '{driver}' is not a supported database driver.")

        # Host and ssl options come from the certificates and instance IP
        # address so we don't want the user to specify them.
        kwargs.pop("host", None)
        kwargs.pop("ssl", None)
        kwargs.pop("port", None)

        # get connection info for AlloyDB instance
        ip_type: str | IPTypes = kwargs.pop("ip_type", self._ip_type)
        # if ip_type is str, convert to IPTypes enum
        if isinstance(ip_type, str):
            ip_type = IPTypes(ip_type.upper())
        ip_address, context = await instance.connection_info(ip_type)

        # callable to be used for auto IAM authn
        def get_authentication_token() -> str:
            """Get OAuth2 access token to be used for IAM database authentication"""
            # refresh credentials if expired
            if not self._credentials.valid:
                request = google.auth.transport.requests.Request()
                self._credentials.refresh(request)
            return self._credentials.token

        # if enable_iam_auth is set, use auth token as database password
        if enable_iam_auth:
            kwargs["password"] = get_authentication_token
        try:
            return await connector(ip_address, context, **kwargs)
        except Exception:
            # we attempt a force refresh, then throw the error
            await instance.force_refresh()
            raise

    async def __aenter__(self) -> Any:
        """Enter async context manager by returning Connector object"""
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Exit async context manager by closing Connector"""
        await self.close()

    async def close(self) -> None:
        """Helper function to cancel Instances' tasks
        and close client."""
        await asyncio.gather(
            *[instance.close() for instance in self._instances.values()]
        )
        if self._client:
            await self._client.close()
