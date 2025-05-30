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

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio

from google.cloud.alloydbconnector import AsyncConnector


async def create_sqlalchemy_engine(
    inst_uri: str,
    user: str,
    password: str,
    db: str,
) -> tuple[sqlalchemy.ext.asyncio.engine.AsyncEngine, AsyncConnector]:
    """Creates a connection pool for an AlloyDB instance and returns the pool
    and the connector. Callers are responsible for closing the pool and the
    connector.

    A sample invocation looks like:

        engine, connector = await create_sqlalchemy_engine(
                inst_uri,
                user,
                password,
                db,
        )
        async with engine.connect() as conn:
            time = await conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
            curr_time = time[0]
            # do something with query result
            await connector.close()

    Args:
        instance_uri (str):
            The instance URI specifies the instance relative to the project,
            region, and cluster. For example:
            "projects/my-project/locations/us-central1/clusters/my-cluster/instances/my-instance"
        user (str):
            The database user name, e.g., postgres
        password (str):
            The database user's password, e.g., secret-password
        db_name (str):
            The name of the database, e.g., mydb
    """
    connector = AsyncConnector()

    # create SQLAlchemy connection pool
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "postgresql+asyncpg://",
        async_creator=lambda: connector.connect(
            inst_uri,
            "asyncpg",
            user=user,
            password=password,
            db=db,
            ip_type="PSC",
        ),
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    return engine, connector


@pytest.mark.asyncio
async def test_connection_with_asyncpg() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_PSC_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_sqlalchemy_engine(inst_uri, user, password, db)

    async with pool.connect() as conn:
        res = (await conn.execute(sqlalchemy.text("SELECT 1"))).fetchone()
        assert res[0] == 1

    await connector.close()
