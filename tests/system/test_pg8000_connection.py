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


# [START alloydb_sqlalchemy_connect_connector]
from datetime import datetime
import os

import pg8000
import sqlalchemy

from google.cloud.alloydb.connector import Connector


def create_sqlalchemy_engine(
    inst_uri: str,
    user: str,
    password: str,
    db: str,
) -> (sqlalchemy.engine.Engine, Connector):
    """Creates a connection pool for an AlloyDB instance and returns the pool
    and the connector. Callers are responsible for closing the pool and the
    connector.

    A sample invocation looks like:

        engine, connector = create_sqlalchemy_engine(
                inst_uri,
                user,
                password,
                db,
        )
        with engine.connect() as conn:
            time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
            conn.commit()
            curr_time = time[0]
            # do something with query result
            connector.close()

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
    connector = Connector()

    def getconn() -> pg8000.dbapi.Connection:
        conn: pg8000.dbapi.Connection = connector.connect(
            inst_uri,
            "pg8000",
            user=user,
            password=password,
            db=db,
        )
        return conn

    # create SQLAlchemy connection pool
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
    )
    engine.dialect.description_encoding = None
    return engine, connector


# [END alloydb_sqlalchemy_connect_connector]


def test_pg8000_time() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_USER"]
    password = os.environ["ALLOYDB_PASS"]
    db = os.environ["ALLOYDB_DB"]

    engine, connector = create_sqlalchemy_engine(inst_uri, user, password, db)
    with engine.connect() as conn:
        time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        conn.commit()
        curr_time = time[0]
        assert type(curr_time) is datetime
    connector.close()
