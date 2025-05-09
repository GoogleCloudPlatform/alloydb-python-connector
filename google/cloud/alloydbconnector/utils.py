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

import re

import aiofiles
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes


async def _write_to_file(
    dir_path: str, ca_cert: str, cert_chain: list[str], key: PrivateKeyTypes
) -> tuple[str, str, str]:
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

    async with aiofiles.open(ca_filename, "w+") as ca_out:
        await ca_out.write(ca_cert)
    async with aiofiles.open(cert_chain_filename, "w+") as chain_out:
        await chain_out.write("".join(cert_chain))
    async with aiofiles.open(key_filename, "wb") as priv_out:
        await priv_out.write(key_bytes)

    return (ca_filename, cert_chain_filename, key_filename)


async def generate_keys() -> tuple[rsa.RSAPrivateKey, str]:
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_key = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("UTF-8")
    )
    return (priv_key, pub_key)


def strip_http_prefix(url: str) -> str:
    """
    Returns a new URL with 'http://' or 'https://' prefix removed.
    """
    m = re.search(r"^(https?://)?(.+)", url)
    if m is None:
        return ""
    return m.group(2)
