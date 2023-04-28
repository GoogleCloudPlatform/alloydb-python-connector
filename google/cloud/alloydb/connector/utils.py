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

from typing import Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _write_to_file(
    dir_path: str, cert_chain: list[str], client_cert: str, key: rsa.RSAPrivateKey
) -> Tuple[str, str, str]:
    """
    Helper function to write the server_ca, client certificate and
    private key to .pem files in a given directory.
    """
    ca_filename = f"{dir_path}/ca.pem"
    cert_chain_filename = f"{dir_path}/chain.pem"
    key_filename = f"{dir_path}/priv.pem"

    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # add client cert to beginning of cert chain
    full_chain = [client_cert] + cert_chain

    with open(ca_filename, "w+") as ca_out:
        ca_out.write("".join(cert_chain))
    with open(cert_chain_filename, "w+") as chain_out:
        chain_out.write("".join(full_chain))
    with open(key_filename, "wb") as priv_out:
        priv_out.write(key_bytes)

    return (ca_filename, cert_chain_filename, key_filename)
