# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import asyncpg
import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine

from google.cloud.alloydb.connector import AsyncConnector


@pytest.mark.asyncio
async def test_connection_with_asyncpg() -> None:
    async with AsyncConnector() as connector:

        async def getconn() -> asyncpg.Connection:
            conn: asyncpg.Connection = await connector.connect(
                os.environ["ALLOYDB_INSTANCE_URI"],
                "asyncpg",
                user=os.environ["ALLOYDB_USER"],
                password=os.environ["ALLOYDB_PASS"],
                db=os.environ["ALLOYDB_DB"],
            )
            return conn

    # create SQLAlchemy connection pool
    pool = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=getconn,
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    async with pool.connect() as conn:
        res = (await conn.execute(sqlalchemy.text("SELECT 1"))).fetchone()
        assert res[0] == 1
