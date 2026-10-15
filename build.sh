#!/usr/bin/env bash

# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Set SCRIPT_DIR to the current directory of this file.
SCRIPT_DIR=$(cd -P "$(dirname "$0")" >/dev/null 2>&1 && pwd)
SCRIPT_FILE="${SCRIPT_DIR}/$(basename "$0")"

##
## Local Development
##
## These functions should be used to run the local development process
##

if [[ ! -d "$SCRIPT_DIR/.venv" ]] ; then
  echo "./.venv not found. Setting up .venv"
  python3 -m venv "$SCRIPT_DIR/.venv"
fi

source "$SCRIPT_DIR/.venv/bin/activate"
export PIP_INDEX_URL=https://pypi.org/simple
export UV_INDEX_URL=https://pypi.org/simple
export UV_LINK_MODE=copy

if ! command -v uv >/dev/null 2>&1 ; then
  python3 -m pip install uv
fi

## clean - Cleans the build output
function clean() {
  if [[ -d "$SCRIPT_DIR/.tools" ]] ; then
    rm -rf "$SCRIPT_DIR/.tools"
  fi
  if [[ -d "$SCRIPT_DIR/dist" ]] ; then
    rm -rf "$SCRIPT_DIR/dist"
  fi
  if [[ -d "$SCRIPT_DIR/build" ]] ; then
    rm -rf "$SCRIPT_DIR/build"
  fi
  if [[ -d "$SCRIPT_DIR/.pytest_cache" ]] ; then
    rm -rf "$SCRIPT_DIR/.pytest_cache"
  fi
  if [[ -f "$SCRIPT_DIR/.coverage" ]] ; then
    rm -f "$SCRIPT_DIR/.coverage"
  fi
}

## build - Builds the project without running tests.
function build() {
  uv run --group lint python -m build
}

## test - Runs local unit tests.
function test() {
  ./scripts/test_unit.sh "$@"
}

## coverage - Runs unit tests and verifies code coverage threshold (>=90%).
function coverage() {
  ./scripts/coverage.sh "$@"
}

## e2e - Runs end-to-end integration tests.
function e2e() {
  if [[ ! -s .envrc ]] ; then
    write_e2e_env .envrc
  fi
  source .envrc
  ./scripts/test_system.sh "$@"
}

## fix - Fixes code format.
function fix() {
  ./scripts/format.sh "$@"
}

## lint - runs the linters
function lint() {
  ./scripts/lint.sh "$@"
}

## deps - updates project dependencies to latest
function deps() {
  uv lock --upgrade 2>/dev/null || true
  echo "Dependencies updated successfully."
}

# write_e2e_env - Loads secrets from the gcloud project and writes
#     them to target/e2e.env to run e2e tests.
function write_e2e_env(){
  # All secrets used by the e2e tests in the form <env_name>=<secret_name>
  secret_vars=(
    ALLOYDB_INSTANCE_URI=ALLOYDB_INSTANCE_URI
    ALLOYDB_PASS=ALLOYDB_CLUSTER_PASS
    ALLOYDB_INSTANCE_IP=ALLOYDB_INSTANCE_IP
    ALLOYDB_IAM_USER=ALLOYDB_PYTHON_IAM_USER
    ALLOYDB_PSC_INSTANCE_URI=ALLOYDB_PSC_INSTANCE_URI
  )

  if [[ -z "${TEST_PROJECT:-}" ]] ; then
    echo "Set TEST_PROJECT environment variable to the project containing"
    echo "the e2e test suite secrets."
    exit 1
  fi

  echo "Getting test secrets from $TEST_PROJECT into $1"
  {
  echo "export ALLOYDB_DB='postgres'"
  echo "export ALLOYDB_USER='postgres'"
  for env_name in "${secret_vars[@]}" ; do
    env_var_name="${env_name%%=*}"
    secret_name="${env_name##*=}"
    set -x
    val=$(gcloud secrets versions access latest --project "$TEST_PROJECT" --secret="$secret_name")
    echo "export $env_var_name='$val'"
  done
  # Aliases for python e2e tests
  echo "export ALLOYDB_INSTANCE_NAME=\"\$ALLOYDB_INSTANCE_URI\""
  } > "$1"
}

## help - prints the help details
##
function help() {
   # This will print the comments beginning with ## above each function
   # in this file.

   echo "build.sh <command> <arguments>"
   echo
   echo "Commands to assist with local development and CI builds."
   echo
   echo "Commands:"
   echo
   grep -e '^##' "$SCRIPT_FILE" | sed -e 's/##/ /'
}

set -euo pipefail

# Check CLI Arguments
if [[ "$#" -lt 1 ]] ; then
  help
  exit 1
fi

cd "$SCRIPT_DIR"

"$@"
