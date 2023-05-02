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
from datetime import datetime
import logging
import ssl
from tempfile import TemporaryDirectory
from typing import List, Tuple

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.cloud.alloydb.connector.utils import _write_to_file

logger = logging.getLogger(name=__name__)

_api_version: str = "v1beta"
# _refresh_buffer is the amount of time before a refresh's result expires
# that a new refresh operation begins.
_refresh_buffer: int = 4 * 60  # 4 minutes


def _create_certificate_request(
    private_key: rsa.RSAPrivateKey,
) -> x509.CertificateSigningRequest:
    csr = (
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
        .sign(private_key, hashes.SHA256())
    )
    return csr


async def _get_metadata(
    client: aiohttp.ClientSession,
    alloydb_api_endpoint: str,
    credentials: Credentials,
    project: str,
    region: str,
    cluster: str,
    name: str,
) -> str:
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
        str: IP Address of the AlloyDB instance.
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

    return resp_dict["ipAddress"]


async def _get_client_certificate(
    client: aiohttp.ClientSession,
    alloydb_api_endpoint: str,
    credentials: Credentials,
    project: str,
    region: str,
    cluster: str,
    key: rsa.RSAPrivateKey,
) -> Tuple[str, List[str]]:
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
        Tuple[str, list[str]]: Tuple containing the client certificate
            and certificate chain for the AlloyDB instance.
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
    csr = csr.public_bytes(encoding=serialization.Encoding.PEM).decode("utf-8")

    data = {
        "pemCsr": csr,
        "certDuration": "3600s",
    }

    resp = await client.post(url, headers=headers, json=data, raise_for_status=True)
    resp_dict = await resp.json()

    return (resp_dict["pemCertificate"], resp_dict["pemCertificateChain"])


def _seconds_until_refresh(expiration: datetime, now: datetime = datetime.now()) -> int:
    """
    Calculates the duration to wait before starting the next refresh.
    Usually the duration will be half of the time until certificate
    expiration.

    Args:
        expiration (datetime.datetime): Time of certificate expiration.
        now (datetime.datetime): Current time. Defaults to datetime.now()
    Returns:
        int: Time in seconds to wait before performing next refresh.
    """

    duration = int((expiration - now).total_seconds())

    # if certificate duration is less than 1 hour
    if duration < 3600:
        # something is wrong with certificate, refresh now
        if duration < _refresh_buffer:
            return 0
        # otherwise wait until 4 minutes before expiration for next refresh
        return duration - _refresh_buffer
    return duration // 2


class RefreshResult:
    """
    Manages the result of a refresh operation.

    Holds the certificates and IP address of an AlloyDB instance.
    Builds the TLS context required to connect to AlloyDB database.

    Args:
        instance_ip (str): The IP address of the AlloyDB instance.
        key (rsa.RSAPrivateKey): Private key for the client connection.
        certs (Tuple[str, List(str)]): Client cert and CA certs for establishing
            the chain of trust used in building the TLS context.
    """

    def __init__(
        self, instance_ip: str, key: rsa.RSAPrivateKey, certs: Tuple[str, List[str]]
    ) -> None:
        self.instance_ip = instance_ip
        self._key = key
        self._certs = certs

        # create TLS context
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # update ssl.PROTOCOL_TLS_CLIENT default
        self.context.check_hostname = False
        # force TLSv1.3
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        # add request_ssl attribute to ssl.SSLContext, required for pg8000 driver
        self.context.request_ssl = False  # type: ignore

        client_cert, cert_chain = self._certs
        # get expiration from client certificate
        cert_obj = x509.load_pem_x509_certificate(client_cert.encode("UTF-8"))
        self.expiration = cert_obj.not_valid_after

        # tmpdir and its contents are automatically deleted after the CA cert
        # and cert chain are loaded into the SSLcontext. The values
        # need to be written to files in order to be loaded by the SSLContext
        with TemporaryDirectory() as tmpdir:
            ca_filename, cert_chain_filename, key_filename = _write_to_file(
                tmpdir, cert_chain, client_cert, self._key
            )
            self.context.load_cert_chain(cert_chain_filename, keyfile=key_filename)
            self.context.load_verify_locations(cafile=ca_filename)


async def _is_valid(task: asyncio.Task[RefreshResult]) -> bool:
    try:
        result = await task
        # valid if current time is before cert expiration
        if datetime.now() < result.expiration:
            return True
    except Exception:
        # suppress any errors from task
        logger.debug("Current refresh result is invalid.")
    return False
