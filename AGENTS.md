# Development Guide

This guide applies to the entire repository and provides context for agents and
human contributors. See [README.md](README.md) for usage examples and
[CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process.

## Background

The AlloyDB Python Connector (`google-cloud-alloydb-connector`) is a Python
library for connecting applications to Google Cloud AlloyDB. It handles IAM
authorization, short-lived client certificates, and TLS 1.3 connections, with
optional automatic IAM database authentication. It supports the pg8000, psycopg
and asyncpg drivers, typically used through SQLAlchemy. psycopg2 is **not**
supported; it appears only in the direct connection tests, which bypass the
connector.

The connector exists to make strong connection security easier to operate.
Standard PostgreSQL drivers support TLS encryption and, when configured,
certificate verification, but do not manage AlloyDB's IAM integration or client
certificate lifecycle. The connector verifies the server certificate and
presents a short-lived client certificate for the AlloyDB server-side proxy to
verify, providing mutual TLS. It combines this with Cloud IAM authorization and
automatic client certificate issuance and rotation. These capabilities are
especially useful in high-security environments that require verified
identities, centrally controlled access, and minimal manual certificate
management. The Go connector is the reference implementation; behavior here
should stay consistent with it.

The connector supplies secure connections; the driver handles the database
protocol, and SQLAlchemy (or the application) manages connection pooling. It
does not create network connectivity. Private IP is the default and requires an
accessible private network; public IP and Private Service Connect (PSC) are
selected with the `ip_type` option. Instance identifiers have this form:

```text
projects/PROJECT/locations/REGION/clusters/CLUSTER/instances/INSTANCE
```

The minimum supported Python version is 3.10 (see `requires-python` in
`pyproject.toml`); `.github/workflows/tests.yaml` lists the tested versions and
platforms. Dependencies are managed with `uv`.

## Architecture

| Location | Responsibility |
| --- | --- |
| `google/cloud/alloydbconnector/connector.py`, `async_connector.py` | Public `Connector` / `AsyncConnector`: lifecycle (`close`), per-instance cache lookup, TLS socket creation, metadata exchange, and handoff to the driver. |
| `.../instance.py`, `lazy.py`, `static.py` | Connection-info caches. `instance.py` (`RefreshAheadCache`) refreshes in the background, `lazy.py` (`LazyRefreshCache`) refreshes on demand, and `static.py` (`StaticConnectionInfoCache`) supplies pre-loaded data without refreshing it. |
| `.../client.py` | `AlloyDBClient`: AlloyDB Admin API calls for instance metadata and client certificates. |
| `.../connection_info.py` | `ConnectionInfo`: cert chain, CA cert, private key, IP addresses and expiration; builds the cached TLS 1.3 `SSLContext` and selects the IP by type. |
| `.../refresh_utils.py`, `rate_limiter.py` | Refresh scheduling and validation; `AsyncRateLimiter` for the refresh-ahead cache. |
| `.../pg8000.py`, `asyncpg.py`, `psycopg.py` | Per-driver connection helpers that take the established socket. |
| `.../enums.py`, `types.py`, `exceptions.py`, `utils.py` | Public enums (`IPTypes`, `RefreshStrategy`), types, exceptions, and utilities including key generation. |
| `google/cloud/alloydb/connector/` | **Backward-compat shim only.** Re-exports everything from `alloydbconnector`; never add logic here. |
| `google/cloud/alloydb_connectors_v1/`, `google/api/` | **Generated protobuf code.** Do not hand-edit; excluded from ruff/mypy. |
| `tests/unit/` | Mocked unit tests (`mocks.py`, `conftest.py`); no network or credentials. |
| `tests/system/` | Live integration tests against a real AlloyDB instance. |
| `scripts/`, `.github/workflows/` | Local format, lint and test commands plus CI. |

A normal connection follows this sequence:

1. `Connector` is created. It resolves credentials and options and generates
   an RSA keypair (`utils.generate_keys`) used for client certificates.
2. `connect` parses the instance URI and retrieves cached connection
   information. The default cache (`RefreshAheadCache`) refreshes metadata and
   certificates in the background; `refresh_strategy="lazy"` refreshes them
   when a connection needs fresh information.
3. `AlloyDBClient` calls the AlloyDB Admin API for the instance's IP addresses
   and a client certificate for the keypair, producing a `ConnectionInfo`.
4. The connector selects the private IP, public IP, or PSC address, opens a
   TCP connection to port 5433, and performs TLS 1.3 authentication using the
   client certificate and instance CA.
5. A metadata exchange runs over the established TLS connection: a protobuf
   request carrying the OAuth2 token and auth type (`DB_NATIVE`, or `AUTO_IAM`
   when IAM database authentication is enabled), answered by a response that
   must be `OK`. The resulting socket is handed to the driver helper, which
   returns the database connection.
6. On failure the cache is invalidated or force-refreshed so the next attempt
   starts from fresh connection information.

**Connector best practices:** Reuse a single `Connector` across connections.
Close it (`close()`, `close_async()` or a context manager) when finished so
background refresh tasks can stop. Host, port and SSL options are supplied by
the connector, so callers must not pass them to the driver.

Two public import paths, one implementation: `google.cloud.alloydb.connector`
(legacy) and `google.cloud.alloydbconnector` (current) must expose identical
public objects, asserted by `tests/system/test_alloydb_connector_package.py`.
If you add a public symbol to `alloydbconnector`, also re-export it from the
legacy shim's `__init__.py`.

## Testing

Run commands from the repository root. Dependencies are installed with `uv`
(`uv sync --group test`, or `uv sync --group lint` which includes test). The
scripts are POSIX shell; on Windows run them with `bash scripts/xxx.sh`. Extra
pytest arguments pass through, e.g. `./scripts/test_unit.sh -k test_connector -x`.

### Unit tests

No Google Cloud credentials or live AlloyDB instance are needed; everything is
mocked. `asyncio_mode = "auto"` is set, so async tests need no
`@pytest.mark.asyncio`.

```sh
# CI-style run with coverage instrumentation.
./scripts/test_unit.sh

# Coverage report; fails if total coverage on google/cloud/alloydbconnector is < 90%.
./scripts/coverage.sh

# Focus on a file or test while iterating.
./scripts/test_unit.sh tests/unit/test_instance.py -x
```

### Integration (system) tests

Each test builds a SQLAlchemy engine through the real `Connector` and driver
and runs `SELECT NOW()`. The query is trivial; the point is exercising the real
certificate refresh and mTLS path for a driver/auth-mode/network-path
combination (public IP, direct IP, PSC; password vs. IAM auth; background vs.
lazy refresh).

Use `.envrc.example` as the configuration template. Set values in your local
environment or an ignored `.envrc` file and load them before running tests.

- Set `ALLOYDB_INSTANCE_URI`, `ALLOYDB_DB`, `ALLOYDB_USER`, `ALLOYDB_PASS` and
  `ALLOYDB_IAM_USER`. The instance needs public IP enabled for public-IP tests,
  and the IAM database user must be configured for IAM authentication.
- Enable the AlloyDB API and provide credentials with access to the instance
  (`gcloud auth application-default login`). The prerequisites in `README.md`
  describe the required IAM roles.
- For the full suite, also set `ALLOYDB_INSTANCE_IP` and
  `ALLOYDB_PSC_INSTANCE_URI` and run from a network that can reach both the
  private IP and the PSC instance (the same VPC).

```sh
# Run without private-network access, using the public-IP test instance.
./scripts/test_system.sh --skip-private-ip

# Run the full suite, including private IP, PSC, and direct connections.
./scripts/test_system.sh
```

`--skip-private-ip` is a pytest option defined in `tests/system/conftest.py`;
`test_system.sh` forwards it to pytest. It skips every test marked
`@pytest.mark.private_ip`: tests that need network access to the instance's
VPC (private IP, PSC and direct connections). Most connector tests connect over
public IP (`ip_type="PUBLIC"`) and still run. The flag does not remove the need
for cloud credentials or a live public-IP instance. A skipped test is not a pass: private IP coverage is
unverified unless those tests ran. Live tests run in CI only on a self-hosted
runner using Workload Identity Federation and Secret Manager, and are skipped
for pull requests from forks and Dependabot.

**An agent without credentials and network access cannot run these.** Do not
fake success: say so explicitly and rely on unit tests, lint and mypy instead.

## Contribution Guidelines

- **Never commit or push on your own.** Do not run `git commit`, `git push`, or
  open/merge a PR unless the developer explicitly asks for that exact action in
  that moment, even after multiple edits, formatting, or passing tests. Leave
  changes unstaged in the working tree for the developer to review and commit.
  Read-only git commands (`status`, `diff`, `log`) are always fine.
- Search existing issues and PRs before starting work. Open an issue first for
  a new feature, public API change, substantial refactor, or a bug whose
  solution needs discussion. Small, well-understood bug fixes, documentation
  corrections and test improvements can go directly to a PR; link any related
  issue and explain the change. Use `SECURITY.md` for security reports rather
  than a public issue.
- Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
  for commit messages and PR titles: `type(optional-scope): short description`.
  [Release Please](https://github.com/googleapis/release-please) parses these
  to prepare version bumps and release notes (configuration in
  `.github/release-please.yml`). Use `feat`, `fix`, `docs`, `test`, `refactor`,
  `ci` or `chore` as appropriate. For example:

  ```text
  feat: add a new connector option
  fix(instance): refresh expired client certificates before connecting
  docs: clarify integration test prerequisites
  ```

  Mark breaking changes with `!` before the colon or a `BREAKING CHANGE:`
  footer and explain the migration. Do not hand-edit `CHANGELOG.md` or
  `google/cloud/alloydbconnector/version.py`.
- Keep PRs small and atomic: one logical change per PR, with its tests and doc
  updates. Split unrelated fixes, refactors and formatting churn into separate
  PRs so each is easy to review and revert, and so each commit type maps cleanly
  to a changelog entry.
- Read the relevant implementation and nearby tests before editing. Keep
  changes focused and preserve unrelated work already present in the checkout.
- Preserve public API compatibility and keep the two import paths identical
  (see Architecture). Update `README.md` when usage changes.
- Follow existing async, error-handling, locking and cleanup patterns. Changes
  to connect or refresh logic should account for cancellation, certificate
  expiry, concurrent callers and resource cleanup. Keep the metadata exchange
  after TLS establishment.
- Add or update tests for behavior changes. Prefer the existing mocks in
  `tests/unit/mocks.py` for unit coverage, and do not introduce cloud
  credentials or live services into unit tests. Live tests belong in
  `tests/system/`; tag any test that needs VPC access (private IP, PSC, direct
  connection) with `@pytest.mark.private_ip` so `--skip-private-ip` skips it,
  and use `ip_type="PUBLIC"` for everything else.
- Code style is enforced by `ruff` (line length 88, target py310; rules E/W, F,
  I with force-single-line imports and first-party `google`, and ANN, so type
  annotations are required outside `tests/`) and `mypy -p google`. Keep new
  code fully typed. Don't use syntax or stdlib features newer than Python 3.10
  in `google/`. Before considering a change done, run:

  ```sh
  ./scripts/format.sh   # ruff check --fix + ruff format, in place; review the diff
  ./scripts/lint.sh     # ruff format --check, ruff check, mypy, sdist build, twine check
  ./scripts/test_unit.sh
  ```

  Ideally also run `./scripts/coverage.sh` (CI requires at least 90%). Only
  claim system tests passed if you actually ran them against a live instance.
- Every source file (`.py`, `.yaml`, `.yml`, `.sh`) needs the Apache-2.0 header
  with `Copyright <year> Google LLC`, enforced by
  `.github/header-checker-lint.yml`. Copy it from a neighboring file, using the
  current year for new files. Do not add `noxfile.py`; extend `scripts/*.sh`.
- Manage dependencies with `uv`: use `uv add` or edit `pyproject.toml` then
  `uv lock`. Never hand-edit `uv.lock` or `.venv`. Keep credentials, tokens,
  passwords and local `.envrc` values out of commits and logs.
- Submit changes through a reviewed pull request and satisfy the Google
  Contributor License Agreement described in `CONTRIBUTING.md`. Explain the
  behavior change and report the checks run, including any integration
  coverage that was not available.
- Follow `CODE_OF_CONDUCT.md` and the community guidance in `CONTRIBUTING.md`.
  Report vulnerabilities through the process in `SECURITY.md`.
