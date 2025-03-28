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

# [START alloydb_sqlalchemy_connect_async_connector]
import asyncpg
import sqlalchemy
import sqlalchemy.ext.asyncio

from google.cloud.alloydb.connector import AsyncConnector


async def create_sqlalchemy_engine(
    inst_uri: str,
    user: str,
    password: str,
    db: str,
    refresh_strategy: str = "background",
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
        db (str):
            The name of the database, e.g., mydb
        refresh_strategy (Optional[str]):
            Refresh strategy for the AlloyDB Connector. Can be one of "lazy"
            or "background". For serverless environments use "lazy" to avoid
            errors resulting from CPU being throttled.
    """
    connector = AsyncConnector(refresh_strategy=refresh_strategy)

    # create SQLAlchemy connection pool
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "postgresql+asyncpg://",
        async_creator=lambda: connector.connect(
            inst_uri,
            "asyncpg",
            user=user,
            password=password,
            db=db,
        ),
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    return engine, connector


# [END alloydb_sqlalchemy_connect_async_connector]


async def create_asyncpg_pool(
    instance_connection_name: str,
    user: str,
    password: str,
    db: str,
    refresh_strategy: str = "background",
) -> tuple[asyncpg.Pool, AsyncConnector]:
    """Creates a native asyncpg connection pool for an AlloyDB instance and
    returns the pool and the connector. Callers are responsible for closing the
    pool and the connector.

    A sample invocation looks like:

        pool, connector = await create_asyncpg_pool(
            inst_conn_name,
            user,
            password,
            db,
        )
        async with pool.acquire() as conn:
            hello = await conn.fetch("SELECT 'Hello World!'")
            # do something with query result
            await connector.close()

    Args:
        instance_connection_name (str):
            The instance connection name specifies the instance relative to the
            project and region. For example: "my-project:my-region:my-instance"
        user (str):
            The database user name, e.g., postgres
        password (str):
            The database user's password, e.g., secret-password
        db (str):
            The name of the database, e.g., mydb
        refresh_strategy (Optional[str]):
            Refresh strategy for the Cloud SQL Connector. Can be one of "lazy"
            or "background". For serverless environments use "lazy" to avoid
            errors resulting from CPU being throttled.
    """
    connector = AsyncConnector(refresh_strategy=refresh_strategy)

    # create native asyncpg pool (requires asyncpg version >=0.30.0)
    pool = await asyncpg.create_pool(
        instance_connection_name,
        connect=lambda instance_connection_name, **kwargs: connector.connect(
            instance_connection_name,
            "asyncpg",
            user=user,
            password=password,
            db=db,
        ),
    )
    return pool, connector


async def test_sqlalchemy_connection_with_asyncpg() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_sqlalchemy_engine(inst_uri, user, password, db)

    async with pool.connect() as conn:
        res = (await conn.execute(sqlalchemy.text("SELECT 1"))).fetchone()
        assert res[0] == 1

    await connector.close()


async def test_lazy_sqlalchemy_connection_with_asyncpg() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_sqlalchemy_engine(
        inst_uri, user, password, db, "lazy"
    )

    async with pool.connect() as conn:
        res = (await conn.execute(sqlalchemy.text("SELECT 1"))).fetchone()
        assert res[0] == 1

    await connector.close()


async def test_connection_with_asyncpg() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_asyncpg_pool(inst_uri, user, password, db)

    async with pool.acquire() as conn:
        res = await conn.fetch("SELECT 1")
        assert res[0][0] == 1

    await connector.close()


async def test_lazy_connection_with_asyncpg() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_asyncpg_pool(inst_uri, user, password, db, "lazy")

    async with pool.acquire() as conn:
        res = await conn.fetch("SELECT 1")
        assert res[0][0] == 1

    await connector.close()
