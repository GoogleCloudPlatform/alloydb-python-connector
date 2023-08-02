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

from datetime import datetime
import os

import pg8000
import sqlalchemy

from google.cloud.alloydb.connector import Connector


def init_connection_engine(connector: Connector) -> sqlalchemy.engine.Engine:
    def getconn() -> pg8000.dbapi.Connection:
        conn: pg8000.dbapi.Connection = connector.connect(
            os.environ["ALLOYDB_INSTANCE_URI"],
            "pg8000",
            user=os.environ["ALLOYDB_USER"],
            password=os.environ["ALLOYDB_PASS"],
            db=os.environ["ALLOYDB_DB"],
        )
        return conn

    # create SQLAlchemy connection pool
    pool = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
    )
    pool.dialect.description_encoding = None
    return pool


def test_pg8000_time() -> None:
    """Basic test to get time from database."""
    with Connector() as connector:
        pool = init_connection_engine(connector)
        with pool.connect() as conn:
            time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
            conn.commit()
            curr_time = time[0]
            assert type(curr_time) is datetime
