# Copyright 2025 Google LLC
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

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import io
import json

from cryptography.hazmat.primitives import serialization

from google.cloud.alloydbconnector.connection_info import ConnectionInfo


class StaticConnectionInfoCache:
    """
    StaticConnectionInfoCache creates a connection info cache that will always
    return a pre-defined connection info. This is a *dev-only* option and
    should not be used in production as it will result in failed connections
    after the client certificate expires. It is also subject to breaking changes
    in the format. NOTE: The static connection info is not refreshed by the
    connector. The JSON format supports multiple instances, regardless of
    cluster.

    This static connection info should hold JSON with the following format:
        {
            "publicKey": "<PEM Encoded public RSA key>",
            "privateKey": "<PEM Encoded private RSA key>",
            "projects/<PROJECT>/locations/<REGION>/clusters/<CLUSTER>/instances/<INSTANCE>": {
                "ipAddress": "<PSA-based private IP address>",
                "publicIpAddress": "<public IP address>",
                "pscInstanceConfig": {
                    "pscDnsName": "<PSC DNS name>"
                },
                "pemCertificateChain": [
                    "<client cert>", "<intermediate cert>", "<CA cert>"
                ],
                "caCert": "<CA cert>"
            }
        }
    """

    def __init__(self, instance_uri: str, static_conn_info: io.TextIOBase) -> None:
        """
        Initializes a StaticConnectionInfoCache instance.

        Args:
            instance_uri (str): The AlloyDB instance's connection URI.
            static_conn_info (io.TextIOBase): The static connection info JSON.
        """
        static_info = json.load(static_conn_info)
        ca_cert = static_info[instance_uri]["caCert"]
        cert_chain = static_info[instance_uri]["pemCertificateChain"]
        dns = ""
        if static_info[instance_uri]["pscInstanceConfig"]:
            dns = static_info[instance_uri]["pscInstanceConfig"]["pscDnsName"].rstrip(
                "."
            )
        ip_addrs = {
            "PRIVATE": static_info[instance_uri]["ipAddress"],
            "PUBLIC": static_info[instance_uri]["publicIpAddress"],
            "PSC": dns,
        }
        expiration = datetime.now(timezone.utc) + timedelta(hours=1)
        priv_key = static_info["privateKey"]
        priv_key_bytes = serialization.load_pem_private_key(
            priv_key.encode("UTF-8"), password=None
        )
        self._info = ConnectionInfo(
            cert_chain, ca_cert, priv_key_bytes, ip_addrs, expiration
        )

    async def force_refresh(self) -> None:
        """
        This is a no-op as the cache holds only static connection information
        and does no refresh.
        """
        pass

    async def connect_info(self) -> ConnectionInfo:
        """
        Retrieves ConnectionInfo instance for establishing a secure
        connection to the AlloyDB instance.
        """
        return self._info

    async def close(self) -> None:
        """
        This is a no-op.
        """
        pass
