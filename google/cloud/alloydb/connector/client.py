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

import logging
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import aiohttp
from google.auth.transport.requests import Request

from google.cloud.alloydb.connector.version import __version__ as version

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

USER_AGENT: str = f"alloydb-python-connector/{version}"
API_VERSION: str = "v1beta"

logger = logging.getLogger(name=__name__)


def _format_user_agent(
    driver: Optional[str],
    custom_user_agent: Optional[str],
) -> str:
    """
    Appends user-defined user agents to the base default agent.
    """
    agent = f"{USER_AGENT}+{driver}" if driver else USER_AGENT
    if custom_user_agent:
        agent = f"{agent} {custom_user_agent}"

    return agent


class AlloyDBClient:
    def __init__(
        self,
        alloydb_api_endpoint: str,
        quota_project: Optional[str],
        credentials: Credentials,
        client: Optional[aiohttp.ClientSession] = None,
        driver: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """
        Establish the client to be used for AlloyDB API requests.

        Args:
            alloydb_api_endpoint (str): Base URL to use when calling
                the AlloyDB API endpoint.
            quota_project (str): The Project ID for an existing Google Cloud
                project. The project specified is used for quota and
                billing purposes.
            credentials (google.auth.credentials.Credentials):
                A credentials object created from the google-auth Python library.
                Must have the AlloyDB Admin scopes. For more info check out
                https://google-auth.readthedocs.io/en/latest/.
            client (aiohttp.ClientSession): Async client used to make requests to
                AlloyDB APIs.
                Optional, defaults to None and creates new client.
            driver (str): Database driver to be used by the client.
        """
        user_agent = _format_user_agent(driver, user_agent)
        headers = {
            "x-goog-api-client": user_agent,
            "User-Agent": user_agent,
            "Content-Type": "application/json",
        }
        if quota_project:
            headers["x-goog-user-project"] = quota_project

        self._client = client if client else aiohttp.ClientSession(headers=headers)
        self._credentials = credentials
        self._alloydb_api_endpoint = alloydb_api_endpoint
        # asyncpg does not currently support using metadata exchange
        # only use metadata exchange for pg8000 driver
        self._use_metadata = True if driver == "pg8000" else False
        self._user_agent = user_agent

    async def _get_metadata(
        self,
        project: str,
        region: str,
        cluster: str,
        name: str,
    ) -> Dict[str, Optional[str]]:
        """
        Fetch the metadata for a given AlloyDB instance.

        Call the AlloyDB APIs connectInfo method to retrieve the
        information about an AlloyDB instance that is used to create secure
        connections.

        Args:
            project (str): Google Cloud project ID that the AlloyDB instance
                resides in.
            region (str): Google Cloud region of the AlloyDB instance.
            cluster (str): The name of the AlloyDB cluster.
            name (str): The name of the AlloyDB instance.

        Returns:
            dict: IP addresses of the AlloyDB instance.
        """
        logger.debug(f"['{project}/{region}/{cluster}/{name}']: Requesting metadata")

        if not self._credentials.valid:
            request = Request()
            self._credentials.refresh(request)

        headers = {
            "Authorization": f"Bearer {self._credentials.token}",
        }

        url = f"{self._alloydb_api_endpoint}/{API_VERSION}/projects/{project}/locations/{region}/clusters/{cluster}/instances/{name}/connectionInfo"

        resp = await self._client.get(url, headers=headers, raise_for_status=True)
        resp_dict = await resp.json()

        return {
            "PRIVATE": resp_dict.get("ipAddress"),
            "PUBLIC": resp_dict.get("publicIpAddress"),
        }

    async def _get_client_certificate(
        self,
        project: str,
        region: str,
        cluster: str,
        pub_key: str,
    ) -> Tuple[str, List[str]]:
        """
        Fetch a client certificate for the given AlloyDB cluster.

        Call the AlloyDB API's generateClientCertificate
        method to create a signed TLS certificate that is authorized to connect via the
        AlloyDB instance's serverside proxy. The cert is valid for twenty-four hours.

        Args:
            project (str): Google Cloud project ID that the AlloyDB instance
                resides in.
            region (str): Google Cloud region of the AlloyDB instance.
            cluster (str): The name of the AlloyDB cluster.
            pub_key (str): PEM-encoded client public key.

        Returns:
            Tuple[str, list[str]]: Tuple containing the CA certificate
                and certificate chain for the AlloyDB instance.
        """
        logger.debug(f"['{project}/{region}/{cluster}']: Requesting client certificate")

        if not self._credentials.valid:
            request = Request()
            self._credentials.refresh(request)

        headers = {
            "Authorization": f"Bearer {self._credentials.token}",
        }

        url = f"{self._alloydb_api_endpoint}/{API_VERSION}/projects/{project}/locations/{region}/clusters/{cluster}:generateClientCertificate"

        data = {
            "publicKey": pub_key,
            "certDuration": "3600s",
            "useMetadataExchange": self._use_metadata,
        }

        resp = await self._client.post(
            url, headers=headers, json=data, raise_for_status=True
        )
        resp_dict = await resp.json()

        return (resp_dict["caCert"], resp_dict["pemCertificateChain"])

    async def close(self) -> None:
        """Close AlloyDBClient gracefully."""
        await self._client.close()
