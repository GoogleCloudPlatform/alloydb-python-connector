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

from datetime import datetime, timedelta
import json
from typing import Any, Callable, Tuple

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class FakeCredentials:
    def __init__(self) -> None:
        self.token = None
        self.expiry = None

    def refresh(self, request: Callable) -> None:
        """Refreshes the access token."""
        self.token = "12345"
        self.expiry = datetime.now() + timedelta(minutes=60)

    @property
    def expired(self) -> bool:
        """Checks if the credentials are expired.

        Note that credentials can be invalid but not expired because
        Credentials with expiry set to None are considered to never
        expire.
        """
        if not self.expiry:
            return False

    @property
    def valid(self) -> bool:
        """Checks the validity of the credentials.

        This is True if the credentials have a token and the token
        is not expired.
        """
        return self.token is not None and not self.expired


async def connectionInfo(request: Any) -> web.Response:
    response = {
        "ipAddress": "127.0.0.1",
        "instanceUid": "123456789",
    }
    return web.Response(content_type="application/json", body=json.dumps(response))


async def generateClientCertificate(request: Any) -> web.Response:
    response = {
        "pemCertificate": "This is the client cert",
        "pemCertificateChain": [
            "This is the intermediate cert",
            "This is the root cert",
        ],
    }
    return web.Response(content_type="application/json", body=json.dumps(response))


def generate_cert(
    common_name: str, expires_in: int = 10
) -> Tuple[x509.CertificateBuilder, rsa.RSAPrivateKey]:
    """
    Generate a private key and cert object to be used in testing.

    Args:
        common_name (str): The Common Name for the certificate.
        expires_in (int): Time in minutes until expiry of certificate.

    Returns:
        Tuple[x509.CertificateBuilder, rsa.RSAPrivateKey]
    """
    # generate private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # calculate expiry time
    now = datetime.now()
    expiration = now + timedelta(minutes=expires_in)
    # configure cert subject
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Mountain View"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Google Inc"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    # build cert
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expiration)
    )
    return cert, key


class FakeInstance:
    """Fake AlloyDB instance to use for testing"""

    def __init__(
        self,
        project: str = "test-project",
        region: str = "test-region",
        cluster: str = "test-cluster",
        name: str = "test-instance",
        ip_address: str = "127.0.0.1",
    ) -> None:
        self.project = project
        self.region = region
        self.cluster = cluster
        self.name = name
        self.ip_address = ip_address
        # build root cert
        self.root_cert, self.root_key = generate_cert("root.alloydb")
        # create self signed root cert
        self.root_cert = self.root_cert.sign(self.root_key, hashes.SHA256())
        # build intermediate cert
        self.intermediate_cert, self.intermediate_key = generate_cert("client.alloydb")
        # create intermediate cert signed by root cert
        self.intermediate_cert = self.intermediate_cert.sign(
            self.root_key, hashes.SHA256()
        )
        # build server cert
        self.server_cert, self.server_key = generate_cert("server.alloydb")
        # create server cert signed by root cert
        self.server_cert = self.server_cert.sign(self.root_key, hashes.SHA256())

    def get_pem_certs(self) -> Tuple[str, str, str]:
        """Helper method to get all certs in pem string format."""
        pem_root = self.root_cert.public_bytes(
            encoding=serialization.Encoding.PEM
        ).decode("UTF-8")
        pem_intermediate = self.intermediate_cert.public_bytes(
            encoding=serialization.Encoding.PEM
        ).decode("UTF-8")
        pem_server = self.server_cert.public_bytes(
            encoding=serialization.Encoding.PEM
        ).decode("UTF-8")
        return (pem_root, pem_intermediate, pem_server)
