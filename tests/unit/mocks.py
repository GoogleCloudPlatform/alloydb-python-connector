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
import io
import ipaddress
import json
import ssl
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth.credentials import TokenState, _helpers
from google.auth.transport import requests

import google.cloud.alloydb_connectors_v1.proto.resources_pb2 as connectorspb
from google.cloud.alloydb.connector.connection_info import ConnectionInfo


class FakeCredentials:
    def __init__(self) -> None:
        self.token: Optional[str] = None
        self.expiry: Optional[datetime] = None

    def refresh(self, _: Callable) -> None:
        """Refreshes the access token."""
        self.token = "12345"
        self.expiry = datetime.now(timezone.utc) + timedelta(minutes=60)

    @property
    def expired(self) -> bool:
        """Checks if the credentials are expired.

        Note that credentials can be invalid but not expired because
        Credentials with expiry set to None are considered to never
        expire.
        """
        return False if not self.expiry else True

    @property
    def token_state(
        self,
    ) -> Literal[TokenState.FRESH, TokenState.STALE, TokenState.INVALID]:
        """
        Tracks the state of a token.
        FRESH: The token is valid. It is not expired or close to expired, or the token has no expiry.
        STALE: The token is close to expired, and should be refreshed. The token can be used normally.
        INVALID: The token is expired or invalid. The token cannot be used for a normal operation.
        """
        if self.token is None:
            return TokenState.INVALID

        # Credentials that can't expire are always treated as fresh.
        if self.expiry is None:
            return TokenState.FRESH

        expired = datetime.now(timezone.utc) >= self.expiry
        if expired:
            return TokenState.INVALID

        is_stale = datetime.now(timezone.utc) >= (
            self.expiry - _helpers.REFRESH_THRESHOLD
        )
        if is_stale:
            return TokenState.STALE

        return TokenState.FRESH


def generate_cert(
    common_name: str, expires_in: int = 60, server_cert: bool = False
) -> tuple[x509.CertificateBuilder, rsa.RSAPrivateKey]:
    """
    Generate a private key and cert object to be used in testing.

    Args:
        common_name (str): The Common Name for the certificate.
        expires_in (int): Time in minutes until expiry of certificate.
        server_cert (bool): Whether it is a server certificate.

    Returns:
        tuple[x509.CertificateBuilder, rsa.RSAPrivateKey]
    """
    # generate private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # calculate expiry time
    now = datetime.now(timezone.utc)
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
    if server_cert:
        cert = cert.add_extension(
            x509.SubjectAlternativeName(
                general_names=[
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("10.0.0.1")),
                    x509.DNSName("x.y.alloydb.goog."),
                ]
            ),
            critical=False,
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
        ip_addrs: dict = {
            "PRIVATE": "127.0.0.1",
            "PUBLIC": "0.0.0.0",
            "PSC": "x.y.alloydb.goog",
        },
        server_name: str = "00000000-0000-0000-0000-000000000000.server.alloydb",
        cert_before: datetime = datetime.now(timezone.utc),
        cert_expiry: datetime = datetime.now(timezone.utc) + timedelta(hours=1),
    ) -> None:
        self.project = project
        self.region = region
        self.cluster = cluster
        self.name = name
        self.ip_addrs = ip_addrs
        self.server_name = server_name
        self.cert_before = cert_before
        self.cert_expiry = cert_expiry

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
        self.server_cert, self.server_key = generate_cert(
            self.server_name, server_cert=True
        )
        # create server cert signed by root cert
        self.server_cert = self.server_cert.sign(self.root_key, hashes.SHA256())

    def get_pem_certs(self) -> tuple[str, str, str]:
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
    
    def generate_pem_certificate_chain(self, pub_key: str) -> tuple[str, List[str]]:
        """Generate the CA certificate and certificate chain for the AlloyDB instance."""
        root_cert, intermediate_cert, server_cert = self.get_pem_certs()
        # encode public key to bytes
        pub_key_bytes: rsa.RSAPublicKey = serialization.load_pem_public_key(
            pub_key.encode("UTF-8"),
        )
        # build client cert
        client_cert = (
            x509.CertificateBuilder()
            .subject_name(self.intermediate_cert.subject)
            .issuer_name(self.intermediate_cert.issuer)
            .public_key(pub_key_bytes)
            .serial_number(x509.random_serial_number())
            .not_valid_before(self.cert_before)
            .not_valid_after(self.cert_expiry)
        )
        # sign client cert with intermediate cert
        client_cert = client_cert.sign(self.intermediate_key, hashes.SHA256())
        client_cert = client_cert.public_bytes(
            encoding=serialization.Encoding.PEM
        ).decode("UTF-8")
        return (server_cert, [client_cert, intermediate_cert, root_cert])
    
    def uri(self) -> str:
        """The URI of the AlloyDB instance."""
        return f"projects/{self.project}/locations/{self.region}/clusters/{self.cluster}/instances/{self.name}"


class FakeAlloyDBClient:
    """Fake class for testing AlloyDBClient"""

    def __init__(
        self, instance: Optional[FakeInstance] = None, driver: str = "pg8000"
    ) -> None:
        self.instance = FakeInstance() if instance is None else instance
        self.closed = False
        self._user_agent = f"test-user-agent+{driver}"
        self._credentials = FakeCredentials()

    async def _get_metadata(self, *args: Any, **kwargs: Any) -> str:
        return self.instance.ip_addrs

    async def _get_client_certificate(
        self,
        project: str,
        region: str,
        cluster: str,
        pub_key: str,
    ) -> tuple[str, list[str]]:
        root_cert, intermediate_cert, server_cert = self.instance.get_pem_certs()
        # encode public key to bytes
        pub_key_bytes: rsa.RSAPublicKey = serialization.load_pem_public_key(
            pub_key.encode("UTF-8"),
        )
        # build client cert
        client_cert = (
            x509.CertificateBuilder()
            .subject_name(self.instance.intermediate_cert.subject)
            .issuer_name(self.instance.intermediate_cert.issuer)
            .public_key(pub_key_bytes)
            .serial_number(x509.random_serial_number())
            .not_valid_before(self.instance.cert_before)
            .not_valid_after(self.instance.cert_expiry)
        )
        # sign client cert with intermediate cert
        client_cert = client_cert.sign(self.instance.intermediate_key, hashes.SHA256())
        client_cert = client_cert.public_bytes(
            encoding=serialization.Encoding.PEM
        ).decode("UTF-8")
        return (server_cert, [client_cert, intermediate_cert, root_cert])

    async def get_connection_info(
        self,
        project: str,
        region: str,
        cluster: str,
        name: str,
        keys: asyncio.Future,
    ) -> ConnectionInfo:
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
        self.closed = True


def metadata_exchange(sock: ssl.SSLSocket) -> None:
    """
        Mimics server side metadata exchange behavior in four steps:

        1. Read a big endian uint32 (4 bytes) from the client. This is the number of
         bytes the message consumes. The length does not include the initial four
         bytes.

        2. Read the message from the client using the message length and serialize
         it into a MetadataExchangeResponse message.

        The real server implementation will then validate the client has connection
        permissions using the provided OAuth2 token based on the auth type. Here in
        the test implementation, the server does nothing.

        3. Prepare a response and write the size of the response as a big endian
         uint32 (4 bytes)

        4. Parse the response to bytes and write those to the client as well.

    Subsequent interactions with the test server use the database protocol.
    """
    # read metadata message length (4 bytes)
    message_len_buffer_size = struct.Struct("I").size
    message_len_buffer = b""
    while message_len_buffer_size > 0:
        chunk = sock.recv(message_len_buffer_size)
        if not chunk:
            raise RuntimeError(
                "Connection closed while getting metadata exchange length!"
            )
        message_len_buffer += chunk
        message_len_buffer_size -= len(chunk)

    (message_len,) = struct.unpack(">I", message_len_buffer)

    # read metadata exchange message
    buffer = b""
    while message_len > 0:
        chunk = sock.recv(message_len)
        if not chunk:
            raise RuntimeError("Connection closed while performing metadata exchange!")
        buffer += chunk
        message_len -= len(chunk)

    # form metadata exchange request to be received from client
    message = connectorspb.MetadataExchangeRequest()
    # parse metadata exchange request from buffer
    message.ParseFromString(buffer)

    # form metadata exchange response to send to client
    resp = connectorspb.MetadataExchangeResponse(
        response_code=connectorspb.MetadataExchangeResponse.OK
    )

    # pack big-endian unsigned integer (4 bytes)
    resp_len = struct.pack(">I", resp.ByteSize())

    # send metadata response length and response message
    sock.sendall(resp_len + resp.SerializeToString())


class FakeConnectionInfo:
    """Fake connection info class that doesn't perform a refresh"""

    def __init__(self) -> None:
        self._close_called = False
        self._force_refresh_called = False

    def connect_info(self) -> Any:
        f = asyncio.Future()
        f.set_result(self)
        return f

    def get_preferred_ip(self, ip_type: Any) -> tuple[str, Any]:
        f = asyncio.Future()
        f.set_result("10.0.0.1")
        return f

    async def create_ssl_context(self) -> None:
        return None

    async def force_refresh(self) -> None:
        self._force_refresh_called = True

    async def close(self) -> None:
        self._close_called = True


def write_static_info(i: FakeInstance) -> io.StringIO:
    """
    Creates a static connection info JSON for the StaticConnectionInfoCache.

    Args:
        i (FakeInstance): The FakeInstance to use to create the CA cert and
            chain.

    Returns:
        io.StringIO
    """
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("UTF-8")
    )
    priv_pem = (
        priv_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("UTF-8")
    )
    ca_cert, chain = i.generate_pem_certificate_chain(pub_pem)
    static = {
        "publicKey": pub_pem,
        "privateKey": priv_pem,
    }
    static[i.uri()] = {
        "pemCertificateChain": chain,
        "caCert": ca_cert,
        "ipAddress": "127.0.0.1", # "private" IP is localhost in testing
        "publicIpAddress": "",
        "pscInstanceConfig": {"pscDnsName": ""},
    }
    return io.StringIO(json.dumps(static))
