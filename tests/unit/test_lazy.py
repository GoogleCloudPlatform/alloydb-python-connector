# Copyright 2024 Google LLC
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

import asyncio
from unittest.mock import patch

import pytest

from google.cloud.alloydbconnector.client import AlloyDBClient
from google.cloud.alloydbconnector.connection_info import ConnectionInfo
from google.cloud.alloydbconnector.exceptions import RefreshError
from google.cloud.alloydbconnector.lazy import LazyRefreshCache
from google.cloud.alloydbconnector.telemetry import REFRESH_FAILURE
from google.cloud.alloydbconnector.telemetry import REFRESH_LAZY_TYPE
from google.cloud.alloydbconnector.telemetry import REFRESH_SUCCESS
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.utils import generate_keys


async def test_LazyRefreshCache_connect_info(fake_client: AlloyDBClient) -> None:
    """
    Test that LazyRefreshCache.connect_info works as expected.
    """
    keys = asyncio.create_task(generate_keys())
    cache = LazyRefreshCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client=fake_client,
        keys=keys,
    )
    # check that cached connection info is empty
    assert cache._cached is None
    conn_info = await cache.connect_info()
    # check that cached connection info is now set
    assert isinstance(cache._cached, ConnectionInfo)
    # check that calling connect_info uses cached info
    conn_info2 = await cache.connect_info()
    assert conn_info2 == conn_info


async def test_LazyRefreshCache_force_refresh(fake_client: AlloyDBClient) -> None:
    """
    Test that LazyRefreshCache.force_refresh works as expected.
    """
    keys = asyncio.create_task(generate_keys())
    cache = LazyRefreshCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client=fake_client,
        keys=keys,
    )
    conn_info = await cache.connect_info()
    # check that cached connection info is now set
    assert isinstance(cache._cached, ConnectionInfo)
    await cache.force_refresh()
    # check that calling connect_info after force_refresh gets new ConnectionInfo
    conn_info2 = await cache.connect_info()
    # check that new connection info was retrieved
    assert conn_info2 != conn_info
    assert cache._cached == conn_info2
    await cache.close()


class _RecordingMetricRecorder(NullMetricRecorder):
    """Captures refresh_count recordings for assertion."""

    def __init__(self) -> None:
        self.refreshes: list[TelemetryAttributes] = []

    def record_refresh_count(self, attrs: TelemetryAttributes) -> None:
        self.refreshes.append(attrs)


async def test_LazyRefreshCache_records_successful_refresh(
    fake_client: AlloyDBClient,
) -> None:
    mr = _RecordingMetricRecorder()
    cache = LazyRefreshCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client=fake_client,
        keys=asyncio.create_task(generate_keys()),
        metric_recorder=mr,
    )
    await cache.connect_info()

    assert [(a.refresh_status, a.refresh_type) for a in mr.refreshes] == [
        (REFRESH_SUCCESS, REFRESH_LAZY_TYPE)
    ]
    await cache.close()


async def test_LazyRefreshCache_records_failed_refresh(
    fake_client: AlloyDBClient,
) -> None:
    mr = _RecordingMetricRecorder()
    cache = LazyRefreshCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client=fake_client,
        keys=asyncio.create_task(generate_keys()),
        metric_recorder=mr,
    )
    with patch.object(
        fake_client, "get_connection_info", side_effect=RefreshError("boom")
    ):
        with pytest.raises(RefreshError):
            await cache.connect_info()

    assert [(a.refresh_status, a.refresh_type) for a in mr.refreshes] == [
        (REFRESH_FAILURE, REFRESH_LAZY_TYPE)
    ]
    await cache.close()


async def test_LazyRefreshCache_without_recorder_does_not_fail(
    fake_client: AlloyDBClient,
) -> None:
    """metric_recorder is optional; omitting it must not break refreshes."""
    cache = LazyRefreshCache(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client=fake_client,
        keys=asyncio.create_task(generate_keys()),
    )
    assert await cache.connect_info() is not None
    await cache.close()
