# Copyright 2024 Google LLC
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
import os
from typing import Tuple

# [START alloydb_sqlalchemy_connect_async_connector_iam_authn]
import asyncpg
import sqlalchemy
import sqlalchemy.ext.asyncio

from google.cloud.alloydb.connector import AsyncConnector


async def create_sqlalchemy_engine(
    inst_uri: str, user: str, db: str, refresh_strategy: str = "background"
) -> Tuple[sqlalchemy.ext.asyncio.engine.AsyncEngine, AsyncConnector]:
    """Creates a connection pool for an AlloyDB instance and returns the pool
    and the connector. Callers are responsible for closing the pool and the
    connector.

    A sample invocation looks like:

        pool, connector = await create_sqlalchemy_engine(
            inst_uri,
            user,
            db,
        )
        async with pool.connect() as conn:
            time = (await conn.execute(sqlalchemy.text("SELECT NOW()"))).fetchone()
            conn.commit()
            curr_time = time[0]
            # do something with query result
            await connector.close()

    Args:
        instance_uri (str):
            The instance URI specifies the instance relative to the project,
            region, and cluster. For example:
            "projects/my-project/locations/us-central1/clusters/my-cluster/instances/my-instance"
        user (str):
            The formatted IAM database username.
            e.g., my-email@test.com, service-account@project-id.iam
        db (str):
            The name of the database, e.g., mydb
        refresh_strategy (Optional[str]):
            Refresh strategy for the AlloyDB Connector. Can be one of "lazy"
            or "background". For serverless environments use "lazy" to avoid
            errors resulting from CPU being throttled.
    """
    connector = AsyncConnector(refresh_strategy=refresh_strategy)

    async def getconn() -> asyncpg.Connection:
        conn: asyncpg.Connection = await connector.connect(
            inst_uri,
            "asyncpg",
            user=user,
            db=db,
            enable_iam_auth=True,
        )
        return conn

    # create async SQLAlchemy connection pool
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "postgresql+asyncpg://",
        async_creator=getconn,
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )
    return engine, connector


# [END alloydb_sqlalchemy_connect_async_connector_iam_authn]


async def test_asyncpg_iam_authn_time() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_IAM_USER"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_sqlalchemy_engine(inst_uri, user, db)
    async with pool.connect() as conn:
        time = (await conn.execute(sqlalchemy.text("SELECT NOW()"))).fetchone()
        curr_time = time[0]
        assert type(curr_time) is datetime
    await connector.close()
    # cleanup AsyncEngine
    await pool.dispose()


async def test_asyncpg_iam_authn_lazy() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_IAM_USER"]
    db = os.environ["ALLOYDB_DB"]

    pool, connector = await create_sqlalchemy_engine(inst_uri, user, db, "lazy")
    async with pool.connect() as conn:
        time = (await conn.execute(sqlalchemy.text("SELECT NOW()"))).fetchone()
        curr_time = time[0]
        assert type(curr_time) is datetime
    await connector.close()
    # cleanup AsyncEngine
    await pool.dispose()
