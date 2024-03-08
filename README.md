<p align="center">
    <a href="https://cloud.google.com/alloydb/docs/connect-language-connectors#python-pg8000">
        <img src="https://raw.githubusercontent.com/GoogleCloudPlatform/alloydb-python-connector/main/docs/images/alloydb-python-connector.png" alt="alloydb-python-connector image">
    </a>
</p>

# AlloyDB Python Connector

[![CI][ci-badge]][ci-build]
[![pypi][pypi-badge]][pypi-docs]
[![python][python-versions]][pypi-docs]

[ci-badge]: https://github.com/GoogleCloudPlatform/alloydb-python-connector/actions/workflows/tests.yaml/badge.svg?event=push
[ci-build]: https://github.com/GoogleCloudPlatform/alloydb-python-connector/actions/workflows/tests.yaml?query=event%3Apush+branch%3Amain
[pypi-badge]: https://img.shields.io/pypi/v/google-cloud-alloydb-connector
[pypi-docs]: https://pypi.org/project/google-cloud-alloydb-connector
[python-versions]: https://img.shields.io/pypi/pyversions/google-cloud-alloydb-connector

The _AlloyDB Python Connector_ is an [AlloyDB](https://cloud.google.com/alloydb)
Connector library designed for use with the Python language.

Using an AlloyDB Connector provides the following benefits:

* **IAM Authorization:** uses IAM permissions to control who/what can connect to
  your AlloyDB instances

* **Improved Security:** uses robust, updated TLS 1.3 encryption and
  identity verification between the client connector and the server-side proxy,
  independent of the database protocol.

* **Convenience:** removes the requirement to use and distribute SSL
  certificates, as well as manage firewalls or source/destination IP addresses.

* (optionally) **IAM DB Authentication:** provides support for
  [AlloyDB’s automatic IAM DB AuthN][iam-db-authn] feature.

[iam-db-authn]: https://cloud.google.com/alloydb/docs/manage-iam-authn

The AlloyDB Python Connector is a package to be used alongside a database driver.
Currently supported drivers are:

* [`pg8000`](https://github.com/tlocke/pg8000)
* [`asyncpg`](https://magicstack.github.io/asyncpg)

## Installation

You can install this library with `pip install`:

### pg8000

```sh
pip install "google-cloud-alloydb-connector[pg8000]"
```

See [Synchronous Driver Usage](#synchronous-driver-usage) for details.

### asyncpg

```sh
pip install "google-cloud-alloydb-connector[asyncpg]"
```

See [Async Driver Usage](#async-driver-usage) for details.

### APIs and Services

This package requires the following to connect successfully:

* IAM principal (user, service account, etc.) with the [AlloyDB
  Client][client-role] role or equivalent. [Credentials](#credentials)
  for the IAM principal are used to authorize connections to an AlloyDB instance.

* The [AlloyDB API][alloydb-api] to be enabled within your Google Cloud
  Project. By default, the API will be called in the project associated with the
  IAM principal.

[alloydb-api]: https://console.cloud.google.com/apis/api/alloydb.googleapis.com
[client-role]: https://cloud.google.com/alloydb/docs/auth-proxy/overview#how-authorized

### Credentials

This library uses the [Application Default Credentials (ADC)][adc] strategy for
resolving credentials. Please see [these instructions for how to set your ADC][set-adc]
(Google Cloud Application vs Local Development, IAM user vs service account credentials),
or consult the [google.auth][google-auth] package.

[adc]: https://cloud.google.com/docs/authentication#adc
[set-adc]: https://cloud.google.com/docs/authentication/provide-credentials-adc
[google-auth]: https://google-auth.readthedocs.io/en/master/reference/google.auth.html

## Usage

This package provides several functions for authorizing and encrypting
connections. These functions are used with your database driver to connect to
your AlloyDB instance.

AlloyDB supports network connectivity through public IP addresses and private,
internal IP addresses. By default this package will attempt to connect over a
private IP connection. When doing so, this package must be run in an
environment that is connected to the [VPC Network][vpc] that hosts your
AlloyDB private IP address.

Please see [Configuring AlloyDB Connectivity][alloydb-connectivity] for more details.

[vpc]: https://cloud.google.com/vpc/docs/vpc
[alloydb-connectivity]: https://cloud.google.com/alloydb/docs/configure-connectivity

### Synchronous Driver Usage

To connect to AlloyDB using the connector, inititalize a `Connector`
object and call it's `connect` method with the proper input parameters.

The `Connector` itself creates database connection objects by calling its `connect` method
but does not manage database connection pooling. For this reason, it is recommended to use
the connector alongside a library that can create connection pools, such as
[SQLAlchemy](https://www.sqlalchemy.org/). This will allow for connections to remain open and
 be reused, reducing connection overhead and the number of connections needed.

In the Connector's `connect` method below, input your AlloyDB instance URI as
the first positional argument and the name of the database driver for the
second positional argument. Insert the rest of your connection keyword arguments
like `user`, `password` and `db` etc.

To use this connector with SQLAlchemy, use the `creator` argument for `sqlalchemy.create_engine`:

```python
from google.cloud.alloydb.connector import Connector
import sqlalchemy

# initialize Connector object
connector = Connector()

# function to return the database connection
def getconn():
    conn = connector.connect(
        "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
        "pg8000",
        user="my-user",
        password="my-password",
        db="my-db-name"
    )
    return conn

# create connection pool
pool = sqlalchemy.create_engine(
    "postgresql+pg8000://",
    creator=getconn,
)
```

The returned connection pool engine can then be used to query and modify the database.

```python
# insert statement
insert_stmt = sqlalchemy.text(
    "INSERT INTO my_table (id, title) VALUES (:id, :title)",
)

with pool.connect() as db_conn:
    # insert into database
    db_conn.execute(insert_stmt, parameters={"id": "book1", "title": "Book One"})

    # query database
    result = db_conn.execute(sqlalchemy.text("SELECT * from my_table")).fetchall()

    # commit transaction (SQLAlchemy v2.X.X is commit as you go)
    db_conn.commit()

    # Do something with the results
    for row in result:
        print(row)
```

To close the `Connector` object's background resources, call it's `close()` method as follows:

```python
connector.close()
```

### Synchronous Context Manager

The `Connector` object can also be used as a context manager in order to
automatically close and cleanup resources, removing the need for explicit
calls to `connector.close()`.

Connector as a context manager:

```python
from google.cloud.alloydb.connector import Connector
import sqlalchemy

# helper function to return SQLAlchemy connection pool
def init_connection_pool(connector: Connector) -> sqlalchemy.engine.Engine:
    # function used to generate database connection
    def getconn():
        conn = connector.connect(
            "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
            "pg8000",
            user="my-user",
            password="my-password",
            db="my-db-name"
        )
        return conn

    # create connection pool
    pool = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
    )
    return pool

# initialize Connector as context manager
with Connector() as connector:
    # initialize connection pool
    pool = init_connection_pool(connector)
    # insert statement
    insert_stmt = sqlalchemy.text(
        "INSERT INTO my_table (id, title) VALUES (:id, :title)",
    )
    
    # interact with AlloyDB database using connection pool
    with pool.connect() as db_conn:
        # insert into database
        db_conn.execute(insert_stmt, parameters={"id": "book1", "title": "Book One"})
    
        # commit transaction (SQLAlchemy v2.X.X is commit as you go)
        db_conn.commit()
    
        # query database
        result = db_conn.execute(sqlalchemy.text("SELECT * from my_table")).fetchall()
    
        # Do something with the results
        for row in result:
            print(row)
```

### Async Driver Usage

The AlloyDB Connector is compatible with [asyncio][] to improve the speed and
efficiency of database connections through concurrency. The `AsyncConnector`
currently supports the following asyncio database drivers:

- [asyncpg](https://magicstack.github.io/asyncpg)

[asyncio]: https://docs.python.org/3/library/asyncio.html

```python
import asyncpg

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from google.cloud.alloydb.connector import AsyncConnector

async def init_connection_pool(connector: AsyncConnector) -> AsyncEngine:
    # initialize Connector object for connections to AlloyDB
    async def getconn() -> asyncpg.Connection:
        conn: asyncpg.Connection = await connector.connect(
            "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
            "asyncpg",
            user="my-user",
            password="my-password",
            db="my-db-name"
            # ... additional database driver args
        )
        return conn

    # The AlloyDB Python Connector can be used along with SQLAlchemy using the
    # 'async_creator' argument to 'create_async_engine'
    pool = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=getconn,
    )
    return pool

async def main():
    connector = AsyncConnector()

    # initialize connection pool
    pool = await init_connection_pool(connector)

    # example query
    async with pool.connect() as conn:
        await conn.execute(sqlalchemy.text("SELECT NOW()"))

    # dispose of connection pool
    await pool.dispose()

    # close Connector
    await connector.close()

```

For more details on additional arguments with an `asyncpg.Connection`, please
visit the [official documentation][asyncpg-docs].


[asyncpg-docs]: https://magicstack.github.io/asyncpg/current/api/index.html

### Async Context Manager

The `AsyncConnector` also may be used as an async context manager, removing the
need for explicit calls to `connector.close()` to cleanup resources.

```python
import asyncio
import asyncpg

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from google.cloud.alloydb.connector import AsyncConnector

async def init_connection_pool(connector: AsyncConnector) -> AsyncEngine:
    # initialize Connector object for connections to AlloyDB
    async def getconn() -> asyncpg.Connection:
            conn: asyncpg.Connection = await connector.connect(
                "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
                "asyncpg",
                user="my-user",
                password="my-password",
                db="my-db-name"
                # ... additional database driver args
            )
            return conn

    # The AlloyDB Python Connector can be used along with SQLAlchemy using the
    # 'async_creator' argument to 'create_async_engine'
    pool = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=getconn,
    )
    return pool

async def main():
    # initialize Connector object for connections to AlloyDB
    async with AsyncConnector() as connector:
        # initialize connection pool
        pool = await init_connection_pool(connector)

        # example query
        async with pool.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT NOW()"))

        # dispose of connection pool
        await pool.dispose()
```

### Automatic IAM Database Authentication

The Python Connector supports [Automatic IAM database authentication][].

Make sure to [configure your AlloyDB Instance to allow IAM authentication][configure-iam-authn]
and [add an IAM database user][add-iam-user].

A `Connector` or `AsyncConnector` can be configured to connect to an AlloyDB instance using
automatic IAM database authentication with the `enable_iam_auth` argument set to `True`.

When configuring the `connector.connect` call for IAM authentication, the `password` field can be
omitted and the `user` field should be formatted as follows:

* For an IAM user account, this is the user's email address.
* For a service account, it is the service account's email without the
`.gserviceaccount.com` domain suffix.

For example, to connect with IAM authentication using the
`test-sa@test-project.iam.gserviceaccount.com` service account:

```python
connector.connect(
    "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
    "pg8000",  # asyncpg for AsyncConnector
    user="test-sa@test-project.iam",
    db="my-db-name",
    enable_iam_auth=True,
)
```

[Automatic IAM database authentication]: https://cloud.google.com/alloydb/docs/manage-iam-authn
[configure-iam-authn]: https://cloud.google.com/alloydb/docs/manage-iam-authn#enable
[add-iam-user]: https://cloud.google.com/alloydb/docs/manage-iam-authn#create-user

### Specifying IP Address Type

The AlloyDB Python Connector by default will attempt to establish connections
to your instance's private IP. To change this, such as connecting to AlloyDB
over a public IP address, set the `ip_type` keyword argument when initializing
a `Connector()` or when calling `connector.connect()`.

Possible values for `ip_type` are `"PRIVATE"` (default value), and `"PUBLIC"`.
Example:

```python
from google.cloud.alloydb.connector import Connector

import sqlalchemy

# initialize Connector object
connector = Connector()

# function to return the database connection
def getconn():
  return connector.connect(
    "projects/<YOUR_PROJECT>/locations/<YOUR_REGION>/clusters/<YOUR_CLUSTER>/instances/<YOUR_INSTANCE>",
    "pg8000",
    user="my-user",
    password="my-password",
    db="my-db-name",
    ip_type="PUBLIC",  # use public IP
  )

# create connection pool
pool = sqlalchemy.create_engine(
    "postgresql+pg8000://",
    creator=getconn,
)

# use connection pool...
connector.close()
```

## Support policy

### Major version lifecycle

This project uses [semantic versioning](https://semver.org/), and uses the
following lifecycle regarding support for a major version:

**Active** - Active versions get all new features and security fixes (that
wouldn’t otherwise introduce a breaking change). New major versions are
guaranteed to be "active" for a minimum of 1 year.
**Deprecated** - Deprecated versions continue to receive security and critical
bug fixes, but do not receive new features. Deprecated versions will be publicly
supported for 1 year.
**Unsupported** - Any major version that has been deprecated for >=1 year is
considered publicly unsupported.

## Supported Python Versions

We follow the [Python Version Support Policy][pyver] used by Google Cloud
Libraries for Python. Changes in supported Python versions will be
considered a minor change, and will be listed in the release notes.

[pyver]: https://cloud.google.com/python/docs/supported-python-versions

### Release cadence

This project aims for a minimum monthly release cadence. If no new
features or fixes have been added, a new PATCH version with the latest
dependencies is released.

### Contributing

We welcome outside contributions. Please see our
[Contributing Guide](CONTRIBUTING.md) for details on how best to contribute.
