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
# versions pyproject.toml allows.
#
# TestExporterCompatibilityGuard covers the other end: no telemetry dependency
# carries an upper bound, so every other CI run resolves to the newest release
# and turns a breaking one into a red build. Nothing otherwise exercises the
# floors -- and a floor that was never installable at all is how
# google-cloud-monitoring>=2.0, every release of which imports pkg_resources,
# went unnoticed. A consumer who pins an older version for reasons of their
# own resolves to exactly this combination.
#
# Two things this deliberately does not do:
#
# It pins the floors here instead of using uv's --resolution lowest-direct,
# which applies to every direct dependency. The ones that predate built-in
# telemetry carry no lower bounds, so that resolves protobuf back to 2.0.3 and
# fails trying to build it. Keep this list in step with the telemetry block in
# pyproject.toml.
#
# It builds its own environment rather than overlaying the project's, because
# google-cloud-monitoring at this floor caps protobuf below what the project's
# interpreter needs and an overlay resolves a protobuf that cannot load.
#
# PYTHON_VERSION is 3.11 on purpose, and 3.11 through 3.13 are the only values
# that work. It is the project's own floor (requires-python >=3.11), and
# google-cloud-monitoring==2.12 is the lowest release that imports on it; on
# 3.14 the lowest is 2.23, where its protobuf cap moves off <5.
# Run this on 3.14 and it fails on protobuf rather than on anything to do with
# the floor under test. That gap is not worth pinning around -- see the
# google-cloud-monitoring comment in pyproject.toml for why -- but it does
# mean this job covers the floor on the versions where the floor is reachable,
# not the oldest install a 3.14 consumer can have.

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
