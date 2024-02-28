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
from datetime import datetime
from datetime import timezone
import logging
import ssl
from tempfile import TemporaryDirectory
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from cryptography import x509

from google.cloud.alloydb.connector.utils import _write_to_file

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger(name=__name__)

# _refresh_buffer is the amount of time before a refresh's result expires
# that a new refresh operation begins.
_refresh_buffer: int = 4 * 60  # 4 minutes


def _seconds_until_refresh(
    expiration: datetime, now: datetime = datetime.now(timezone.utc)
) -> int:
    """
    Calculates the duration to wait before starting the next refresh.
    Usually the duration will be half of the time until certificate
    expiration.

    Args:
        expiration (datetime.datetime): Time of certificate expiration.
        now (datetime.datetime): Current time (UTC)
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
        ip_addrs (Dict[str, str]): The IP addresses of the AlloyDB instance.
        key (rsa.RSAPrivateKey): Private key for the client connection.
        certs (Tuple[str, List(str)]): Client cert and CA certs for establishing
            the chain of trust used in building the TLS context.
    """

    def __init__(
        self,
        ip_addrs: Dict[str, Optional[str]],
        key: rsa.RSAPrivateKey,
        certs: Tuple[str, List[str]],
    ) -> None:
        self.ip_addrs = ip_addrs
        # create TLS context
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # update ssl.PROTOCOL_TLS_CLIENT default
        self.context.check_hostname = False
        # force TLSv1.3
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        # add request_ssl attribute to ssl.SSLContext, required for pg8000 driver
        self.context.request_ssl = False  # type: ignore
        # unpack certs
        ca_cert, cert_chain = certs
        # get expiration from client certificate
        cert_obj = x509.load_pem_x509_certificate(cert_chain[0].encode("UTF-8"))
        self.expiration = cert_obj.not_valid_after_utc

        # tmpdir and its contents are automatically deleted after the CA cert
        # and cert chain are loaded into the SSLcontext. The values
        # need to be written to files in order to be loaded by the SSLContext
        with TemporaryDirectory() as tmpdir:
            ca_filename, cert_chain_filename, key_filename = _write_to_file(
                tmpdir, ca_cert, cert_chain, key
            )
            self.context.load_cert_chain(cert_chain_filename, keyfile=key_filename)
            self.context.load_verify_locations(cafile=ca_filename)


async def _is_valid(task: asyncio.Task) -> bool:
    try:
        result = await task
        # valid if current time is before cert expiration
        if datetime.now(timezone.utc) < result.expiration:
            return True
    except Exception:
        # suppress any errors from task
        logger.debug("Current refresh result is invalid.")
    return False
