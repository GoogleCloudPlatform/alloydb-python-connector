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
from typing import Any, Callable

from aiohttp import web


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
