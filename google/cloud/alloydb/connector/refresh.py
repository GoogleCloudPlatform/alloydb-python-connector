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

import logging
from typing import Any, Dict

import aiohttp
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud.alloydb.connector.exceptions import RefreshError

logger = logging.getLogger(name=__name__)

_api_version: str = "v1beta"


def _create_certificate_request(private_key: rsa.RSAPrivateKey) -> str:
    csr_obj = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "alloydb-connector"),
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "CA"),
                    x509.NameAttribute(NameOID.LOCALITY_NAME, "Sunnyvale"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Google LLC"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Cloud"),
                ]
            )
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )
    csr = csr_obj.public_bytes(encoding=serialization.Encoding.PEM).decode("utf-8")
    return csr


async def _get_metadata(
    client: aiohttp.ClientSession,
    alloydb_api_endpoint: str,
    credentials: Credentials,
    project: str,
    region: str,
    cluster: str,
    name: str,
) -> Dict[str, Any]:
    """
    Fetch the metadata for a given AlloyDB instance.

    Call the AlloyDB Admin APIs connectInfo method to retrieve the
    information about an AlloyDB instance that is used to create secure
    connections.

    Args:
        client (aiohttp.ClientSession): Async client used to make
            requests to AlloyDB Admin APIs.
        alloydb_api_endpoint (str): Base URL to use when calling
            the AlloyDB API endpoint.
        credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            Must have the AlloyDB Admin scopes. For more info check out
            https://google-auth.readthedocs.io/en/latest/.
        project (str): Google Cloud project ID that the AlloyDB instance
            resides in.
        region (str): Google Cloud region of the AlloyDB instance.
        cluster (str): The name of the AlloyDB cluster.
        name (str): The name of the AlloyDB instance.

    Returns:
        Dict[str, str]: Dictionary containing the IP address
            and instance UID of the AlloyDB instance.
    """
    logger.debug(f"['{project}/{region}/{cluster}/{name}']: Requesting metadata")

    if not credentials.valid:
        request = Request()
        credentials.refresh(request)

    headers = {
        "Authorization": f"Bearer {credentials.token}",
    }

    url = f"{alloydb_api_endpoint}/{_api_version}/projects/{project}/locations/{region}/clusters/{cluster}/instances/{name}/connectionInfo"

    resp = await client.get(url, headers=headers, raise_for_status=True)
    resp_dict = await resp.json()

    return {
        "ip_address": resp_dict["ipAddress"],
        "uid": resp_dict["instanceUid"],
    }


async def _get_client_certificate(
    client: aiohttp.ClientSession,
    alloydb_api_endpoint: str,
    credentials: Credentials,
    project: str,
    region: str,
    cluster: str,
    key: rsa.RSAPrivateKey,
) -> Dict[str, Any]:
    """
    Fetch a client certificate for the given AlloyDB cluster.

    Call the AlloyDB Admin API's generateClientCertificate
    method to create a signed TLS certificate that is authorized to connect via the
    AlloyDB instance's serverside proxy. The cert is valid for twenty-four hours.

    Args:
        client (aiohttp.ClientSession): Async client used to make
            requests to AlloyDB Admin APIs.
        alloydb_api_endpoint (str): Base URL to use when calling
            the AlloyDB API endpoint.
        credentials (google.auth.credentials.Credentials):
            A credentials object created from the google-auth Python library.
            Must have the AlloyDB Admin scopes. For more info check out
            https://google-auth.readthedocs.io/en/latest/.
        project (str): Google Cloud project ID that the AlloyDB instance
            resides in.
        region (str): Google Cloud region of the AlloyDB instance.
        cluster (str): The name of the AlloyDB cluster.
        key (rsa.RSAPrivateKey): Client private key used in refresh operation
            to generate client certificate.

    Returns:
        Dict[str, str]: Dictionary containing the certificates
            for the AlloyDB instance.
    """
    logger.debug(f"['{project}/{region}/{cluster}']: Requesting client certificate")

    if not credentials.valid:
        request = Request()
        credentials.refresh(request)

    headers = {
        "Authorization": f"Bearer {credentials.token}",
    }

    url = f"{alloydb_api_endpoint}/{_api_version}/projects/{project}/locations/{region}/clusters/{cluster}:generateClientCertificate"

    # create the certificate signing request
    csr = _create_certificate_request(key)

    data = {
        "pemCsr": csr,
        "certDuration": "3600s",
    }

    resp = await client.post(url, headers=headers, json=data, raise_for_status=True)
    resp_dict = await resp.json()

    # There should always be two certs in the chain. If this fails, the API has
    # broken its contract with the client.
    if len(resp_dict["pemCertificateChain"]) != 2:
        raise RefreshError("missing instance and root certificates")
    return {
        "client_cert": resp_dict["pemCertificate"],
        "intermediate_cert": resp_dict["pemCertificateChain"][0],
        "root_cert": resp_dict["pemCertificateChain"][1],
    }
