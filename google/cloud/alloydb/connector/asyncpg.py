"""
Copyright 2023 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import asyncio
import struct
import ssl
import socket
import functools
from typing import Any, TYPE_CHECKING

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
import google.cloud.alloydb.connectors.v1.resources_pb2 as pb

SERVER_PROXY_PORT = 5433

if TYPE_CHECKING:
    import asyncpg


class MetadataExchangeProtocol(asyncio.Protocol):
    def __init__(self, loop):
        self.on_data = loop.create_future()

    def data_received(self, data):
        # TODO: What if the data isn't fully received?
        # read 4 bytes to determine message length
        resp_size = struct.unpack(">I", data[:4])[0]
        # now parse the message from the data
        resp_raw = data[4:4+resp_size]
        resp = pb.MetadataExchangeResponse()
        resp.ParseFromString(resp_raw)

        if resp.response_code == pb.MetadataExchangeResponse.OK:
            self.on_data.set_result(True)
        else:
            self.on_data.set_exception(Exception(resp.error))

    def connection_lost(self, exc):
        if not self.on_data.done():
            if exc is None:
                exc = Exception("failed to complete mdx")
            self.on_data.set_exception(exc)


async def _simple_factory(proto_factory, host, port, loop=None, ssl=None):
    return await loop.create_connection(proto_factory, host, port, ssl=ssl)


def get_authentication_token(credentials: Credentials) -> str:
    """Get OAuth2 access token to be used for IAM database authentication"""
    # refresh credentials if expired
    if not credentials.valid:
        request = Request()
        credentials.refresh(request)
    return credentials.token


async def _connector_factory(proto_factory, host, port, loop=None, ssl=None):
    tr, pr = await loop.create_connection(
        lambda: MetadataExchangeProtocol(loop),
        host, port,
        ssl=ssl,
        server_hostname=host,
    )

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    req = pb.MetadataExchangeRequest(
        user_agent="pyprotoscratch/v0.0.0",
        oauth2_token=get_authentication_token(creds),
        auth_type=pb.MetadataExchangeRequest.DB_NATIVE,
    )
    req_serialized = req.SerializeToString()
    req_serialized_len = len(req_serialized)
    req_packed_len = struct.pack(">I", req_serialized_len)
    tr.write(req_packed_len)
    tr.write(req_serialized)

    try:
        await pr.on_data
    except (Exception):
        tr.close()
        raise

    conn_factory = functools.partial(
        loop.create_connection, proto_factory,
        ssl=ssl,
        server_hostname=host,
    )

    sock = _get_socket(tr)
    sock = sock.dup()
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    tr.close()

    try:
        new_tr, pg_proto = await conn_factory(sock=sock)
        return new_tr, pg_proto
    except (Exception, asyncio.CancelledError):
        sock.close()
        raise


def _get_socket(transport):
    sock = transport.get_extra_info('socket')
    if sock is None:
        # Shouldn't happen with any asyncio-complaint event loop.
        raise ConnectionError(
            'could not get the socket for transport {!r}'.format(transport))
    return sock


async def connect(
    ip_address: str, ctx: ssl.SSLContext, **kwargs: Any
) -> "asyncpg.Connection":
    """Helper function to create an asyncpg DB-API connection object.

    :type ip_address: str
    :param ip_address: A string containing an IP address for the AlloyDB
        instance.

    :type ctx: ssl.SSLContext
    :param ctx: An SSLContext object created from the AlloyDB server CA
        cert and ephemeral cert.

    :type kwargs: Any
    :param kwargs: Keyword arguments for establishing asyncpg connection
        object to AlloyDB instance.

    :rtype: asyncpg.Connection
    :returns: An asyncpg.Connection object to an AlloyDB instance.
    """
    try:
        import asyncpg
    except ImportError:
        raise ImportError(
            'Unable to import module "asyncpg." Please install and try again.'
        )
    user = kwargs.pop("user")
    db = kwargs.pop("db")
    passwd = kwargs.pop("password", None)

    return await asyncpg.connect(
        user=user,
        database=db,
        password=passwd,
        host=ip_address,
        port=SERVER_PROXY_PORT,
        ssl=ctx,
        # direct_tls=True,
        connector_factory=_connector_factory,
        **kwargs,
    )
