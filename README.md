<p align="center">
    <a href="https://cloud.google.com/alloydb/docs/connect-language-connectors#python-pg8000">
        <img src="https://raw.githubusercontent.com/GoogleCloudPlatform/alloydb-python-connector/main/docs/images/alloydb-python-connector.png" alt="alloydb-python-connector image">
    </a>
</p>

# AlloyDB Python Connector

[![CI][ci-badge]][ci-build]
[![pypi][pypi-badge]][pypi-docs]
[![pypi][pypi-downloads]][pypi-docs]
[![python][python-versions]][pypi-docs]

[ci-badge]: https://github.com/GoogleCloudPlatform/alloydb-python-connector/actions/workflows/tests.yaml/badge.svg?event=push
[ci-build]: https://github.com/GoogleCloudPlatform/alloydb-python-connector/actions/workflows/tests.yaml?query=event%3Apush+branch%3Amain
[pypi-badge]: https://img.shields.io/pypi/v/google-cloud-alloydb-connector
[pypi-docs]: https://pypi.org/project/google-cloud-alloydb-connector
[pypi-downloads]: https://img.shields.io/pypi/dm/google-cloud-alloydb-connector
[python-versions]: https://img.shields.io/pypi/pyversions/google-cloud-alloydb-connector

The AlloyDB Python Connector is the recommended way to connect to AlloyDB from
Python applications. It provides:

- **Secure connections** — TLS 1.3 encryption and identity verification,
  independent of the database protocol
- **IAM-based authorization** — controls who can connect to your AlloyDB
  instances using Google Cloud IAM
- **No certificate management** — no SSL certificates, firewall rules, or IP
  allowlisting required
- **IAM database authentication** — optional support for
  [automatic IAM DB authentication][iam-db-authn]

[iam-db-authn]: https://cloud.google.com/alloydb/docs/manage-iam-authn

**Supported drivers:** [`pg8000`][pg8000] (sync) · [`psycopg`][psycopg] (sync) · [`asyncpg`][asyncpg] (async)

[pg8000]: https://codeberg.org/tlocke/pg8000
[psycopg]: https://www.psycopg.org/
[asyncpg]: https://magicstack.github.io/asyncpg

## Quickstart

### Sync (pg8000 + SQLAlchemy)

**Install:**
```sh
pip install "google-cloud-alloydb-connector[pg8000]" sqlalchemy
```

**Connect:**
```python
import sqlalchemy
from google.cloud.alloydbconnector import Connector

INSTANCE_URI = "projects/MY_PROJECT/locations/MY_REGION/clusters/MY_CLUSTER/instances/MY_INSTANCE"

with Connector() as connector:
    pool = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=lambda: connector.connect(
            INSTANCE_URI,
            "pg8000",
            user="my-user",
            password="my-password",
            db="my-db",
        ),
    )

    with pool.connect() as conn:
        result = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        print(result)
```

### Sync (psycopg + SQLAlchemy)

**Install:**
```sh
pip install "google-cloud-alloydb-connector[psycopg]" sqlalchemy
```

**Connect:**
```python
import sqlalchemy
from google.cloud.alloydbconnector import Connector

INSTANCE_URI = "projects/MY_PROJECT/locations/MY_REGION/clusters/MY_CLUSTER/instances/MY_INSTANCE"

with Connector() as connector:
    pool = sqlalchemy.create_engine(
        "postgresql+psycopg://",
        creator=lambda: connector.connect(
            INSTANCE_URI,
            "psycopg",
            user="my-user",
            password="my-password",
            db="my-db",
        ),
    )

    with pool.connect() as conn:
        result = conn.execute(sqlalchemy.text("SELECT NOW()")).fetchone()
        print(result)
```

### Async (asyncpg)

**Install:**
```sh
pip install "google-cloud-alloydb-connector[asyncpg]"
```

**Connect:**

```python
import asyncio
import asyncpg
from google.cloud.alloydbconnector import AsyncConnector

INSTANCE_URI = "projects/MY_PROJECT/locations/MY_REGION/clusters/MY_CLUSTER/instances/MY_INSTANCE"

async def main():
    async with AsyncConnector() as connector:
        pool = await asyncpg.create_pool(
            INSTANCE_URI,
            connect=lambda instance_connection_name, **kwargs: connector.connect(
                instance_connection_name,
                "asyncpg",
                user="my-user",
                password="my-password",
                db="my-db",
            ),
        )

        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT NOW()")
            print(result)

        await pool.close()

asyncio.run(main())
```

### Async (asyncpg + SQLAlchemy)

**Install:**
```sh
pip install "google-cloud-alloydb-connector[asyncpg]" sqlalchemy
```

**Connect:**
```python
import asyncio
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine
from google.cloud.alloydbconnector import AsyncConnector

INSTANCE_URI = "projects/MY_PROJECT/locations/MY_REGION/clusters/MY_CLUSTER/instances/MY_INSTANCE"

async def main():
    async with AsyncConnector() as connector:
        pool = create_async_engine(
            "postgresql+asyncpg://",
            async_creator=lambda: connector.connect(
                INSTANCE_URI,
                "asyncpg",
                user="my-user",
                password="my-password",
                db="my-db",
            ),
        )

        async with pool.connect() as conn:
            result = await conn.execute(sqlalchemy.text("SELECT NOW()"))
            print(result.fetchone())

        await pool.dispose()

asyncio.run(main())
```

## Prerequisites

1. **Enable the AlloyDB API** in your Google Cloud project:
   [console.cloud.google.com/apis/api/alloydb.googleapis.com][alloydb-api]

2. **Grant IAM permissions:** your principal needs the [AlloyDB Client][client-role] role
   (or equivalent) on the instance.

3. **Set up credentials** using [Application Default Credentials (ADC)][adc]:
   ```sh
   gcloud auth application-default login
   ```

[alloydb-api]: https://console.cloud.google.com/apis/api/alloydb.googleapis.com
[client-role]: https://cloud.google.com/alloydb/docs/auth-proxy/overview#how-authorized
[adc]: https://cloud.google.com/docs/authentication#adc

> **Note:** By default the connector uses private IP. Run your code from an
> environment connected to the VPC that hosts your AlloyDB instance, or see
> [Configuring AlloyDB Connectivity][alloydb-connectivity] for other options.

[alloydb-connectivity]: https://cloud.google.com/alloydb/docs/configure-connectivity

## Configuration

### Connector Lifecycle

Create **one connector per application** and reuse it for the lifetime of the
process. Each connector maintains a background refresh cycle that keeps
connection credentials warm, so creating one per request would waste resources
and cause unnecessary latency.

The recommended approach is a context manager — `close()` is called
automatically, even if an exception is raised:

```python
# Sync
with Connector() as connector:
    ...

# Async
async with AsyncConnector() as connector:
    ...
```

For long-lived applications (e.g. a web server) where the connector outlives
any single block, call `close()` explicitly at shutdown:

```python
# Sync
connector = Connector()
...
connector.close()

# Async
connector = AsyncConnector()
...
await connector.close()
```

### IP Address Type

Connect over private IP (default), public IP, or Private Service Connect (PSC):

```python
# At the Connector level (applies to all connections)
connector = Connector(ip_type="PUBLIC")

# Or per connection
connector.connect(INSTANCE_URI, "pg8000", ..., ip_type="PSC")
```

Valid values: `"PRIVATE"` (default), `"PUBLIC"`, `"PSC"`.

### IAM Database Authentication

Skip the password and authenticate using your IAM identity instead. First,
[enable IAM auth on your instance][configure-iam-authn] and [create an IAM
database user][add-iam-user].

```python
connector.connect(
    INSTANCE_URI,
    "pg8000",  # or "psycopg" (sync) / "asyncpg" (async)
    user="service-account@my-project.iam",  # omit .gserviceaccount.com suffix
    db="my-db",
    enable_iam_auth=True,
)
```

For IAM user accounts, use the full email address as `user`.

[configure-iam-authn]: https://cloud.google.com/alloydb/docs/manage-iam-authn#enable
[add-iam-user]: https://cloud.google.com/alloydb/docs/manage-iam-authn#create-user

### Lazy Refresh (Cloud Run, Cloud Functions)

In serverless environments where CPU may be throttled between requests, use
`refresh_strategy="lazy"` to fetch connection info on demand instead of
running a background refresh cycle:

```python
connector = Connector(refresh_strategy="lazy")
```

### Debug Logging

```python
import logging

logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s")
logging.getLogger("google.cloud.alloydbconnector").setLevel(logging.DEBUG)
```

## Import Paths

This package supports two equivalent import paths:

```python
from google.cloud.alloydbconnector import Connector  # preferred
from google.cloud.alloydb.connector import Connector  # also supported
```

The first is preferred to avoid namespace collisions with the
[google-cloud-alloydb][alloydb-py-lib] package.

[alloydb-py-lib]: https://github.com/googleapis/google-cloud-python/tree/main/packages/google-cloud-alloydb

## Support Policy

### Major Version Lifecycle

This project uses [semantic versioning](https://semver.org/):

- **Active** — receives all new features and security fixes. New major versions are guaranteed active for a minimum of 1 year.
- **Deprecated** — receives security and critical bug fixes only, for 1 year after deprecation.
- **Unsupported** — any major version deprecated for ≥1 year.

### Supported Python Versions

Follows the [Python Version Support Policy][pyver] used by Google Cloud
Libraries for Python. Changes in supported Python versions are treated as
minor changes and listed in the release notes.

[pyver]: https://cloud.google.com/python/docs/supported-python-versions

### Release Cadence

This project targets a minimum monthly release cadence. If no new features or
fixes have been added, a new PATCH version with the latest dependencies is
released.

### Contributing

We welcome outside contributions. Please see our
[Contributing Guide](CONTRIBUTING.md) for details on how best to contribute.
