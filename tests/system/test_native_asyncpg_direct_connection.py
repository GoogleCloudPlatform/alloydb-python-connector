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

# flake8: noqa: ANN001
from datetime import datetime
import os

# [START alloydb_native_asyncpg_connect_iam_authn_direct]
import asyncpg

import google.auth
from google.auth.transport.requests import Request

# [END alloydb_native_asyncpg_connect_iam_authn_direct]


async def test_native_asyncpg_time() -> None:
    """Basic test to get time from database using native asyncpg connection."""
    ip_address = os.environ["ALLOYDB_INSTANCE_IP"]  # Private IP for AlloyDB instance
    user = os.environ["ALLOYDB_IAM_USER"]
    db = os.environ["ALLOYDB_DB"]

    # [START alloydb_native_asyncpg_connect_iam_authn_direct]
    # initialize Google Auth credentials
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    def get_authentication_token() -> str:
        """Get OAuth2 access token to be used for IAM database authentication"""
        # refresh credentials if expired
        if not credentials.valid:
            request = Request()
            credentials.refresh(request)
        return credentials.token

    # ... inside of async context (function)
    async with asyncpg.create_pool(
        user=user,  # your IAM db user, e.g. service-account@project-id.iam
        password=get_authentication_token,  # callable to get fresh OAuth2 token
        host=ip_address,  # your AlloyDB instance IP address
        port=5432,
        database=db,  # your database name
        # Because this connection uses an OAuth2 token as a password, you must
        # require SSL, or better, enforce all clients speak SSL on the server
        # side. This ensures the OAuth2 token is not inadvertantly leaked.
        ssl="require",
    ) as pool:
        # acquire connection from native asyncpg connection pool
        async with pool.acquire() as conn:
            time = await conn.fetchrow("SELECT NOW()")
            print("Current time is ", time[0])
            # [END alloydb_native_asyncpg_connect_iam_authn_direct]
            assert type(time[0]) is datetime
