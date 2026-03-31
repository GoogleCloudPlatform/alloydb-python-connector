# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import os

import nox

RUFF_VERSION = "ruff==0.11.2"
LINT_PATHS = ["google", "tests", "noxfile.py"]

SYSTEM_TEST_PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
UNIT_TEST_PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]


@nox.session
def lint(session):
    """Run linters.

    Returns a failure if the linters find linting errors or sufficiently
    serious code quality issues.
    """
    session.install("-r", "requirements-test.txt")
    session.install("-r", "requirements.txt")
    session.install(
        RUFF_VERSION,
        "mypy",
        "build",
        "twine",
    )
    session.run(
        "ruff",
        "format",
        "--check",
        "--diff",
        *LINT_PATHS,
    )
    session.run(
        "ruff",
        "check",
        *LINT_PATHS,
    )
    session.run(
        "mypy",
        "-p",
        "google",
        "--install-types",
        "--non-interactive",
        "--show-traceback",
    )
    # verify that pyproject.toml is valid
    session.run("python", "-m", "build", "--sdist")
    session.run("twine", "check", "--strict", "dist/*")


@nox.session
def format(session):
    """Format code with ruff."""
    session.install(RUFF_VERSION)
    session.run(
        "ruff",
        "check",
        "--fix",
        *LINT_PATHS,
    )
    session.run(
        "ruff",
        "format",
        *LINT_PATHS,
    )


@nox.session()
def cover(session):
    """Run the final coverage report.

    This outputs the coverage report aggregating coverage from the unit
    test runs (not system test runs), and then erases coverage data.
    """
    session.install("coverage", "pytest-cov")
    session.run("coverage", "report", "--show-missing", "--fail-under=0")

    session.run("coverage", "erase")


def default(session, path):
    # Install all test dependencies, then install this package in-place.
    session.install("-r", "requirements-test.txt")
    session.install(".")
    session.install("-r", "requirements.txt")
    # Run pytest with coverage.
    # Using the coverage command instead of `pytest --cov`, because
    # `pytest ---cov` causes the module to be initialized twice, which returns
    # this error: "ImportError: PyO3 modules compiled for CPython 3.8 or older
    # may only be initialized once per interpreter process". More info about
    # this is stated here: https://github.com/pytest-dev/pytest-cov/issues/614.
    session.run(
        "coverage",
        "run",
        "--include=*/google/cloud/alloydbconnector/*.py",
        "-m",
        "pytest",
        "-v",
        path,
        *session.posargs,
    )
    session.run(
        "coverage",
        "xml",
        "-o",
        "sponge_log.xml",
    )


@nox.session(python=UNIT_TEST_PYTHON_VERSIONS)
def unit(session):
    """Run the unit test suite."""
    default(session, os.path.join("tests", "unit"))


@nox.session(python=SYSTEM_TEST_PYTHON_VERSIONS)
def system(session):
    default(session, os.path.join("tests", "system"))
