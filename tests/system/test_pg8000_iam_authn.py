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

# [START alloydb_sqlalchemy_connect_connector_iam_authn]
import sqlalchemy

from google.cloud.alloydbconnector import Connector


def create_sqlalchemy_engine(
    inst_uri: str, user: str, db: str, refresh_strategy: str = "background"
) -> tuple[sqlalchemy.engine.Engine, Connector]:
    """Creates a connection pool for an AlloyDB instance and returns the pool
    and the connector. Callers are responsible for closing the pool and the
    connector.

    A sample invocation looks like:

        engine, connector = create_sqlalchemy_engine(
            inst_uri,
            user,
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
            The formatted IAM database username.
            e.g., my-email@test.com, service-account@project-id.iam
        db (str):
            The name of the database, e.g., mydb
        refresh_strategy (Optional[str]):
            Refresh strategy for the AlloyDB Connector. Can be one of "lazy"
            or "background". For serverless environments use "lazy" to avoid
            errors resulting from CPU being throttled.
    """
    connector = Connector(refresh_strategy=refresh_strategy)

    # create SQLAlchemy connection pool
    engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=lambda: connector.connect(
            inst_uri,
            "pg8000",
            user=user,
            db=db,
            enable_iam_auth=True,
        ),
    )
    return engine, connector


# [END alloydb_sqlalchemy_connect_connector_iam_authn]


def test_pg8000_iam_authn_time() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_IAM_USER"]
    db = os.environ["ALLOYDB_DB"]

    engine, connector = create_sqlalchemy_engine(inst_uri, user, db)
    with engine.connect() as conn:
        time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        conn.commit()
        curr_time = time[0]
        assert type(curr_time) is datetime
    connector.close()


def test_pg8000_iam_authn_lazy() -> None:
    """Basic test to get time from database."""
    inst_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    user = os.environ["ALLOYDB_IAM_USER"]
    db = os.environ["ALLOYDB_DB"]

    engine, connector = create_sqlalchemy_engine(inst_uri, user, db, "lazy")
    with engine.connect() as conn:
        time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        conn.commit()
        curr_time = time[0]
        assert type(curr_time) is datetime
    connector.close()
