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

The AlloyDB Python Connector is a package to be used alongside a database driver.
Currently supported drivers are:

* [`pg8000`](https://github.com/tlocke/pg8000)

## Installation

You can install this library with `pip install`:

```sh
pip install "google-cloud-alloydb-connector[pg8000]"
```

## Usage

This package provides several functions for authorizing and encrypting
connections. These functions are used with your database driver to connect to
your AlloyDB instance.

AlloyDB supports network connectivity through private, internal IP addresses only. 
This package must be run in an environment that is connected to the
[VPC Network][vpc] that hosts your AlloyDB private IP address.

Please see [Configuring AlloyDB Connectivity][alloydb-connectivity] for more details.

[vpc]: https://cloud.google.com/vpc/docs/vpc
[alloydb-connectivity]: https://cloud.google.com/alloydb/docs/configure-connectivity

### APIs and Services

This package requires the following to connect successfully:

* IAM principal (user, service account, etc.) with the [AlloyDB
  Client][client-role] role or equivalent. [Credentials](#credentials)
  for the IAM principal are used to authorize connections to an AlloyDB instance.

* The [AlloyDB Admin API][admin-api] to be enabled within your Google Cloud
  Project. By default, the API will be called in the project associated with the
  IAM principal.

[admin-api]:   https://console.cloud.google.com/apis/api/alloydb.googleapis.com
[client-role]: https://cloud.google.com/alloydb/docs/auth-proxy/overview#how-authorized

### Credentials

This library uses the [Application Default Credentials (ADC)][adc] strategy for
resolving credentials. Please see [these instructions for how to set your ADC][set-adc]
(Google Cloud Application vs Local Development, IAM user vs service account credentials),
or consult the [google.auth][google-auth] package.

[adc]: https://cloud.google.com/docs/authentication#adc
[set-adc]: https://cloud.google.com/docs/authentication/provide-credentials-adc
[google-auth]: https://google-auth.readthedocs.io/en/master/reference/google.auth.html

### How to use this Connector

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

### Using Connector as a Context Manager

The `Connector` object can also be used as a context manager in order to
automatically close and cleanup resources, removing the need for explicit
calls to `connector.close()`.

Connector as a context manager:

```python
from google.cloud.alloydb.connector import Connector
import sqlalchemy

# build connection
def getconn():
    with Connector() as connector:
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
