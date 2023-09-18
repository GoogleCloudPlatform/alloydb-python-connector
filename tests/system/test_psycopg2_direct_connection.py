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

# fmt: off
# [START alloydb_psycopg2_connect_iam_authn_direct]
import sqlalchemy
from sqlalchemy import event

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

# initialize Google Auth creds
creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def get_authentication_token(credentials: Credentials) -> str:
    """Get OAuth2 access token to be used for IAM database authentication"""
    # refresh credentials if expired
    if not credentials.valid:
        request = Request()
        credentials.refresh(request)
    return str(credentials.token)

# [END alloydb_psycopg2_connect_iam_authn_direct]
# fmt: on


def create_sqlalchemy_engine(
    ip_address: str,
    user: str,
    db_name: str,
) -> sqlalchemy.engine.Engine:
    """Creates a SQLAlchemy connection pool for an AlloyDB instance configured
    using psycopg2.

    Callers are responsible for closing the pool. This implementation uses a
    direct TCP connection with IAM database authentication and not
    the Cloud SQL Python Connector.

    A sample invocation looks like:

        engine = create_sqlalchemy_engine(
                ip_address,
                user,
                db,
        )

        with engine.connect() as conn:
            time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
            conn.commit()

    Args:
        ip_address (str):
            The IP address of an AlloyDB instance, e.g., 10.0.0.1
        user (str):
            The formatted IAM database username.
            e.g., my-email@test.com, service-account@project-id.iam
        db_name (str):
            The name of the database, e.g., mydb
    """
    # [START alloydb_psycopg2_connect_iam_authn_direct]

    engine = sqlalchemy.create_engine(
        # Equivalent URL:
        # postgresql+psycopg2://<user>:empty@<host>:5432/<db_name>
        sqlalchemy.engine.url.URL.create(
            drivername="postgresql+psycopg2",
            username=user,  # IAM db user, e.g. service-account@project-id.iam
            password="empty",  # placeholder to be replaced with OAuth2 token
            host=ip_address,  # AlloyDB instance IP address
            port=5432,
            database=db_name,  # "my-database-name"
        ),
        connect_args={"sslmode": "require"},
    )

    # [END alloydb_psycopg2_connect_iam_authn_direct]
    return engine


def test_psycopg2_time() -> None:
    """Basic test to get time from database."""
    ip_address = os.environ["ALLOYDB_INSTANCE_IP"]  # Private IP for AlloyDB instance
    user = os.environ["ALLOYDB_USER"]
    db = os.environ["ALLOYDB_DB"]

    engine = create_sqlalchemy_engine(ip_address, user, db)
    # fmt: off
    # [START alloydb_psycopg2_connect_iam_authn_direct]
    # set 'do_connect' event listener to replace password with OAuth2 token
    event.listens_for(engine, "do_connect")
    def auto_iam_authentication(dialect, conn_rec, cargs, cparams) -> None:
        cparams["password"] = os.environ["ALLOYDB_PASS"]

    # use connection from connection pool to query Cloud SQL database
    with engine.connect() as conn:
        time = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        conn.commit()
        print("Current time is ", time[0])
        # [END alloydb_psycopg2_connect_iam_authn_direct]
        # fmt: on
        curr_time = time[0]
        assert type(curr_time) is datetime
