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
import logging
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import aiohttp
from cryptography import x509
from google.auth.credentials import TokenState
from google.auth.transport import requests

from google.cloud.alloydb.connector.connection_info import ConnectionInfo
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
    if custom_user_agent and isinstance(custom_user_agent, str):
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
        headers = {
            "Authorization": f"Bearer {self._credentials.token}",
        }

        url = f"{self._alloydb_api_endpoint}/{API_VERSION}/projects/{project}/locations/{region}/clusters/{cluster}/instances/{name}/connectionInfo"

        resp = await self._client.get(url, headers=headers)
        # try to get response json for better error message
        try:
            resp_dict = await resp.json()
            if resp.status >= 400:
                # if detailed error message is in json response, use as error message
                message = resp_dict.get("error", {}).get("message")
                if message:
                    resp.reason = message
        # skip, raise_for_status will catch all errors in finally block
        except Exception:
            pass
        finally:
            resp.raise_for_status()

        # Remove trailing period from PSC DNS name.
        psc_dns = resp_dict.get("pscDnsName")
        if psc_dns:
            psc_dns = psc_dns.rstrip(".")

        return {
            "PRIVATE": resp_dict.get("ipAddress"),
            "PUBLIC": resp_dict.get("publicIpAddress"),
            "PSC": psc_dns,
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
        headers = {
            "Authorization": f"Bearer {self._credentials.token}",
        }

        url = f"{self._alloydb_api_endpoint}/{API_VERSION}/projects/{project}/locations/{region}/clusters/{cluster}:generateClientCertificate"

        data = {
            "publicKey": pub_key,
            "certDuration": "3600s",
            "useMetadataExchange": self._use_metadata,
        }

        resp = await self._client.post(url, headers=headers, json=data)
        # try to get response json for better error message
        try:
            resp_dict = await resp.json()
            if resp.status >= 400:
                # if detailed error message is in json response, use as error message
                message = resp_dict.get("error", {}).get("message")
                if message:
                    resp.reason = message
        # skip, raise_for_status will catch all errors in finally block
        except Exception:
            pass
        finally:
            resp.raise_for_status()

        return (resp_dict["caCert"], resp_dict["pemCertificateChain"])

    async def get_connection_info(
        self,
        project: str,
        region: str,
        cluster: str,
        name: str,
        keys: asyncio.Future,
    ) -> ConnectionInfo:
        """Immediately performs a full refresh operation using the AlloyDB API.

        Args:
            project (str): The name of the project the AlloyDB instance is
                located in.
            region (str): The region the AlloyDB instance is located in.
            cluster (str): The cluster the AlloyDB instance is located in.
            name (str): Name of the AlloyDB instance.
            keys (asyncio.Future): A future to the client's public-private key
                pair.

        Returns:
            ConnectionInfo: All the information required to connect securely to
                the AlloyDB instance.
        """
        priv_key, pub_key = await keys

        # before making AlloyDB API calls, refresh creds if required
        if not self._credentials.token_state == TokenState.FRESH:
            self._credentials.refresh(requests.Request())

        # fetch metadata
        metadata_task = asyncio.create_task(
            self._get_metadata(
                project,
                region,
                cluster,
                name,
            )
        )
        # generate client and CA certs
        certs_task = asyncio.create_task(
            self._get_client_certificate(
                project,
                region,
                cluster,
                pub_key,
            )
        )

        ip_addrs, certs = await asyncio.gather(metadata_task, certs_task)

        # unpack certs
        ca_cert, cert_chain = certs
        # get expiration from client certificate
        cert_obj = x509.load_pem_x509_certificate(cert_chain[0].encode("UTF-8"))
        expiration = cert_obj.not_valid_after_utc

        return ConnectionInfo(
            cert_chain,
            ca_cert,
            priv_key,
            ip_addrs,
            expiration,
        )

    async def close(self) -> None:
        """Close AlloyDBClient gracefully."""
        logger.debug("Waiting for connector's http client to close")
        await self._client.close()
        logger.debug("Closed connector's http client")
