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
import socket
import ssl
from tempfile import TemporaryDirectory
from typing import Any, Callable, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from google.cloud.alloydb.connector.refresh import RefreshResult
from google.cloud.alloydb.connector.utils import _write_to_file


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
        server_name: str = "00000000-0000-0000-0000-000000000000.server.alloydb",
        cert_expiry: datetime = datetime.now() + timedelta(hours=1),
    ) -> None:
        self.project = project
        self.region = region
        self.cluster = cluster
        self.name = name
        self.ip_address = ip_address
        self.server_name = server_name
        self.cert_expiry = cert_expiry

    def generate_certs(self) -> None:
        """
        Build certs required for chain of trust with testing server.
        """
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
        self.server_cert, self.server_key = generate_cert(self.server_name)
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

    def configure_tls(self) -> None:
        """
        Configure fake server with TLS as specified by the FakeInstance.
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # create TLS context
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # tmpdir and its contents are automatically deleted after the CA cert
        # and cert chain are loaded into the SSLcontext. The values
        # need to be written to files in order to be loaded by the SSLContext
        with TemporaryDirectory() as tmpdir:
            pem_root, _, pem_server = self.get_pem_certs()
            ca_filename, _, key_filename = _write_to_file(
                tmpdir, [pem_server, pem_root], "", self.server_key
            )
            context.load_cert_chain(ca_filename, key_filename)
        server = context.wrap_socket(server, server_side=True)
        self.server = server

    def start_server_proxy(self) -> None:
        """
        Starts a fake server proxy and listens on the provided port
        on all interfaces, configured with TLS as specified by the
        FakeInstance.
        """
        self.server.bind((self.ip_address, 5433))
        self.server.listen(0)
        self.server.accept()


class BadRefresh(Exception):
    """Error to throw for tests."""

    pass


class FakeRefreshResult(RefreshResult):
    """Fake class for testing RefreshResult"""

    def __init__(self, expiration: datetime, instance_ip: str = "127.0.0.1") -> None:
        self.expiration = expiration
        self.instance_ip = instance_ip


async def refresh_success(*args: Any, **kwargs: Any) -> FakeRefreshResult:
    return FakeRefreshResult(datetime.now() + timedelta(minutes=10))


async def refresh_expired(*args: Any, **kwargs: Any) -> FakeRefreshResult:
    return FakeRefreshResult(datetime.now() - timedelta(minutes=10))


async def refresh_error(*args: Any, **kwargs: Any) -> None:
    raise BadRefresh("something went wrong...")
