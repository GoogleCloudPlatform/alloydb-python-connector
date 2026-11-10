#!/usr/bin/env bash
# Copyright 2026 Google LLC
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

# Run the built-in telemetry tests against the oldest telemetry dependency
# versions pyproject.toml allows. Keep the pins below in step with it.
#
# The pins are listed here because uv's --resolution lowest-direct also
# lowers dependencies with no floor (protobuf resolves to an unbuildable
# 2.0.3). The venv is separate because google-cloud-monitoring at its floor
# caps protobuf below what newer interpreters need, which is also why this
# runs on 3.11; it works through 3.13.

set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
VENV="$(mktemp -d)/floors"

uv venv --python "${PYTHON_VERSION}" "${VENV}"

uv pip install --python "${VENV}/bin/python" --prerelease=allow \
    "opentelemetry-api==1.30" \
    "opentelemetry-sdk==1.30" \
    "opentelemetry-exporter-gcp-monitoring==1.11.0a0" \
    "google-cloud-monitoring==2.12" \
    pytest pytest-asyncio mock \
    --editable .

"${VENV}/bin/python" -m pytest -v tests/unit/test_telemetry.py "$@"
