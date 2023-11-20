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


BLACK_VERSION = "black==22.3.0"
LINT_PATHS = ["google", "tests", "noxfile.py", "setup.py"]

SYSTEM_TEST_PYTHON_VERSIONS = ["3.8", "3.9", "3.10", "3.11"]
UNIT_TEST_PYTHON_VERSIONS = ["3.8", "3.9", "3.10", "3.11"]


@nox.session
def lint(session):
    """Run linters.

    Returns a failure if the linters find linting errors or sufficiently
    serious code quality issues.
    """
    session.install("-r", "requirements-test.txt")
    session.install("-r", "requirements.txt")
    session.install(
        "flake8",
        "flake8-import-order",
        "flake8-annotations",
        "mypy",
        BLACK_VERSION,
        "types-setuptools",
        "twine",
    )
    session.run(
        "black",
        "--check",
        *LINT_PATHS,
    )
    session.run(
        "flake8",
        "--import-order-style=google",
        "--application-import-names=google,tests",
        "google",
        "tests",
    )
    session.run(
        "mypy",
        "-p",
        "google",
        "--install-types",
        "--non-interactive",
        "--show-traceback",
    )
    session.run("python", "setup.py", "sdist")
    session.run("twine", "check", "dist/*")


@nox.session
def blacken(session):
    """Run black.

    Format code to uniform standard.
    """
    session.install(BLACK_VERSION)
    session.run(
        "black",
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
    session.install("-e", ".")
    session.install("-r", "requirements.txt")
    # Run pytest with coverage.
    session.run(
        "pytest",
        "--cov=google.cloud.alloydb.connector",
        "-v",
        "--cov-config=.coveragerc",
        "--cov-report=",
        "--cov-fail-under=0",
        "--junitxml=sponge_log.xml",
        path,
        *session.posargs,
    )


@nox.session(python=UNIT_TEST_PYTHON_VERSIONS)
def unit(session):
    """Run the unit test suite."""
    default(session, os.path.join("tests", "unit"))


@nox.session(python=SYSTEM_TEST_PYTHON_VERSIONS)
def system(session):
    default(session, os.path.join("tests", "system"))
