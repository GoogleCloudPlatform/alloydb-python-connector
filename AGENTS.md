# AGENTS.md

Guidance for AI agents (and anyone else) working on `google-cloud-alloydb-connector`.

## Git — never commit or push on your own

Do not run `git commit`, `git push`, or open/merge a PR unless the developer
explicitly asks for that exact action in that moment. This holds even if a
task involves multiple file edits, formatting, or passing tests — finishing
the work means leaving the changes unstaged/uncommitted in the working tree
for the developer to review and commit themselves. The developer is the only
one who pushes code. Read-only git commands (`status`, `diff`, `log`) are
always fine.

## What this is

A Python client library that lets drivers (pg8000, asyncpg, psycopg2, psycopg)
connect securely to Google Cloud AlloyDB, by managing mTLS certificates and
opening encrypted sockets to instances. Package is published as
`google-cloud-alloydb-connector`.

## Package layout

- `google/cloud/alloydbconnector/` — the real implementation. Edit here.
  - `connector.py` / `async_connector.py` — `Connector` / `AsyncConnector` entry points.
  - `instance.py`, `client.py`, `refresh_utils.py` — cert refresh & instance metadata logic.
  - `pg8000.py`, `asyncpg.py`, `psycopg.py` — per-driver connection helpers.
  - `enums.py`, `types.py`, `exceptions.py`, `rate_limiter.py`, `lazy.py`, `static.py`, `utils.py`.
- `google/cloud/alloydb/connector/` — a **backward-compat shim only**. It just
  re-exports everything from `alloydbconnector`. Never add new logic here —
  only import and re-export if a new public symbol needs to stay available
  under the old path.
- `google/cloud/alloydb_connectors_v1/` and `google/api/` — **generated
  protobuf code**. Do not hand-edit; excluded from ruff/mypy in `pyproject.toml`.
- `tests/unit/` — mocked, no network/cloud access. Fast, run constantly.
- `tests/system/` — real end-to-end tests against a live AlloyDB instance.
  See below.
- `scripts/` — the actual entry points for lint/format/test/coverage (CI calls
  these directly; there is **no `noxfile.py`** despite CONTRIBUTING.md still
  mentioning `nox` — that doc is stale, trust `scripts/` instead).

## Setup

Dependencies and the virtualenv are managed with `uv` (not pip/poetry directly).

```bash
uv sync --group test   # installs runtime + test deps into .venv
uv sync --group lint   # installs lint deps too (includes test group)
```

Don't hand-edit `.venv`; don't add dependencies by editing `uv.lock` directly —
use `uv add` / edit `pyproject.toml` then `uv lock`.

## Running things

Always go through the scripts (they wrap `uv run` with the right group/flags):

```bash
./scripts/format.sh    # ruff check --fix + ruff format, in place
./scripts/lint.sh       # ruff format --check, ruff check, mypy, build sdist, twine check
./scripts/test_unit.sh  # pytest tests/unit, with coverage instrumentation
./scripts/coverage.sh   # runs unit tests, prints coverage report, FAILS if total < 90%
./scripts/test_system.sh  # pytest tests/system — REQUIRES real AlloyDB access, see below
```

On Windows, run these with `bash scripts/xxx.sh` (git-bash) — they're POSIX shell scripts.

Extra pytest args pass through, e.g. `./scripts/test_unit.sh -k test_connector -x`.

## Code conventions

- Formatting/linting: `ruff` (line length 88, target py310). Rule set: pycodestyle
  (E/W), pyflakes (F), isort (I, force-single-line imports, first-party = `google`),
  flake8-annotations (ANN) — type annotations are required on non-test code.
  `tests/*` is exempt from ANN.
- Type checking: `mypy -p google` (python_version 3.10, namespace packages,
  `ignore_missing_imports = true`). Keep new code fully typed.
- Every source file (`.py`, `.yaml`, `.yml`, `.sh`) needs the Apache-2.0 header
  block with `Copyright <year> Google LLC` at the top — enforced by
  `.github/header-checker-lint.yml` in CI. Copy the header verbatim from a
  neighboring file (use the current year for genuinely new files).
- `asyncio_mode = "auto"` in pytest config — async test functions don't need
  `@pytest.mark.asyncio`.
- Minimum supported Python is 3.10; don't use syntax/stdlib features newer than that
  in `google/` (test code can be less strict but stay consistent).
- No `noxfile.py` — don't reintroduce one; use/extend `scripts/*.sh` instead.
- Don't extract a helper function/method for logic that's only used in one
  place. Write it directly, inline, in the function that needs it. Only pull
  something out into its own helper if it's genuinely called from multiple
  call sites (or already is, elsewhere in the codebase). A single function
  with flat, linear logic is preferred over one that's just a thin wrapper
  calling a chain of one-off private helpers — don't split for its own sake.

## Testing model — important distinction

- **Unit tests** (`tests/unit/`): everything is mocked (see `tests/unit/mocks.py`,
  `tests/unit/conftest.py`). No network access, no GCP credentials needed. This
  is what you should run after almost any change.
- **System tests** (`tests/system/`): hit a **real** AlloyDB cluster over the
  network using real credentials. Each test builds a SQLAlchemy engine via the
  actual `Connector`/driver and runs `SELECT NOW()` — the query is trivial, the
  point is exercising the real cert-refresh + mTLS connection path for a given
  driver/auth-mode/network-path combination (public IP, direct IP, PSC;
  password auth vs. IAM auth; background vs. lazy refresh).
  - Needs env vars from `.envrc.example` (`ALLOYDB_INSTANCE_URI`, `ALLOYDB_USER`,
    `ALLOYDB_PASS`, `ALLOYDB_DB`, `ALLOYDB_IAM_USER`, `ALLOYDB_INSTANCE_IP`,
    `ALLOYDB_PSC_INSTANCE_URI`, etc.) plus `gcloud auth application-default login`,
    and must run from inside the same VPC as the instance.
  - **An agent without that access cannot run these** — don't attempt to fake
    success. Say so explicitly and rely on unit tests + lint + mypy instead.
  - In CI these only run on a self-hosted runner with Workload Identity
    Federation + Secret Manager, and are skipped for fork/dependabot PRs.

## Before considering a change done

1. `./scripts/format.sh`
2. `./scripts/lint.sh` (ruff + mypy + sdist build/twine check must all pass)
3. `./scripts/test_unit.sh` (and ideally `./scripts/coverage.sh` — CI requires ≥90%
   coverage on `google/cloud/alloydbconnector/*.py`)
4. Only run/claim system tests if you actually have live AlloyDB credentials
   and network access; otherwise state that they're unverified.

## Versioning / releases

Releases are automated via `release-please` (`.github/release-please.yml`,
`CHANGELOG.md`). Use **Conventional Commits** style commit messages
(`fix:`, `feat:`, `chore:`, `refactor:`, `docs:`, etc.) — release-please parses
these to generate the changelog and bump `google/cloud/alloydbconnector/version.py`.
Don't hand-edit `CHANGELOG.md` or `version.py`.

## Two public import paths, one implementation

`google.cloud.alloydb.connector` (legacy) and `google.cloud.alloydbconnector`
(current) must always expose identical public objects — this is asserted by
`tests/system/test_alloydb_connector_package.py`. If you add a new public
symbol to `alloydbconnector`, also re-export it from the legacy shim's
`__init__.py`.
