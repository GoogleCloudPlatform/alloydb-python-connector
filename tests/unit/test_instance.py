# Copyright 2023 Google LLC
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
from datetime import datetime
from datetime import timedelta
from typing import Tuple

import aiohttp
from mocks import FakeAlloyDBClient
import pytest

from google.cloud.alloydb.connector.exceptions import IPTypeNotFoundError
from google.cloud.alloydb.connector.exceptions import RefreshError
from google.cloud.alloydb.connector.instance import _parse_instance_uri
from google.cloud.alloydb.connector.instance import Instance
from google.cloud.alloydb.connector.instance import IPTypes
from google.cloud.alloydb.connector.refresh import _is_valid
from google.cloud.alloydb.connector.refresh import RefreshResult
from google.cloud.alloydb.connector.utils import generate_keys


@pytest.mark.parametrize(
    "instance_uri, expected",
    [
        (
            "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
            ("test-project", "test-region", "test-cluster", "test-instance"),
        ),
        (
            "projects/test-domain:test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
            (
                "test-domain:test-project",
                "test-region",
                "test-cluster",
                "test-instance",
            ),
        ),
    ],
)
def test_parse_instance_uri(
    instance_uri: str, expected: Tuple[str, str, str, str]
) -> None:
    """
    Test that _parse_instance_uri works correctly on
    normal instance uri and domain-scoped projects.
    """
    assert expected == _parse_instance_uri(instance_uri)


def test_parse_bad_instance_uri() -> None:
    """
    Tests that ValueError is thrown for bad instance uri.
    """
    with pytest.raises(ValueError):
        _parse_instance_uri("test-project:test-instance")


@pytest.mark.asyncio
async def test_Instance_init() -> None:
    """
    Test to check whether the __init__ method of Instance
    can tell if the instance URI that's passed in is formatted correctly.
    """
    keys = asyncio.create_task(generate_keys())
    async with aiohttp.ClientSession() as client:
        instance = Instance(
            "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
            client,
            keys,
        )
        assert (
            instance._project == "test-project"
            and instance._region == "test-region"
            and instance._cluster == "test-cluster"
            and instance._name == "test-instance"
        )


@pytest.mark.asyncio
async def test_Instance_init_invalid_instant_uri() -> None:
    """
    Test to check whether the __init__ method of Instance
    will throw error for invalid instance URI.
    """
    keys = asyncio.create_task(generate_keys())
    async with aiohttp.ClientSession() as client:
        with pytest.raises(ValueError):
            Instance("invalid/instance/uri/", client, keys)


@pytest.mark.asyncio
async def test_Instance_close() -> None:
    """
    Test that Instance's close method
    cancels tasks gracefully.
    """
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # make sure tasks aren't cancelled
    assert instance._current.cancelled() is False
    assert instance._next.cancelled() is False
    # run close() to cancel tasks
    await instance.close()
    # verify tasks are cancelled
    assert (instance._current.done() or instance._current.cancelled()) is True
    assert instance._next.cancelled() is True


@pytest.mark.asyncio
async def test_perform_refresh() -> None:
    """Test that _perform refresh returns valid RefreshResult"""
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    refresh = await instance._perform_refresh()
    assert refresh.ip_addrs == {
        "PRIVATE": "127.0.0.1",
        "PUBLIC": "0.0.0.0",
        "PSC": "x.y.alloydb.goog",
    }
    assert refresh.expiration == client.instance.cert_expiry.replace(microsecond=0)
    # close instance
    await instance.close()


@pytest.mark.parametrize(
    "ip_type, expected",
    [
        (
            IPTypes.PRIVATE,
            "127.0.0.1",
        ),
        (
            IPTypes.PUBLIC,
            "0.0.0.0",
        ),
        (
            IPTypes.PSC,
            "x.y.alloydb.goog",
        ),
    ],
)
@pytest.mark.asyncio
async def test_connection_info(ip_type: IPTypes, expected: str) -> None:
    """Test that connection_info returns proper ip address."""
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    ip_address, _ = await instance.connection_info(ip_type=ip_type)
    assert ip_address == expected
    # close instance
    await instance.close()


@pytest.mark.asyncio
async def test_connection_info_IPTypeNotFoundError() -> None:
    """Test that connection_info throws IPTypeNotFoundError"""
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    # set ip_addrs to have no public IP
    client.instance.ip_addrs = {"PRIVATE": "10.0.0.1"}
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # check RefreshError is thrown
    with pytest.raises(IPTypeNotFoundError):
        await instance.connection_info(ip_type=IPTypes.PUBLIC)
    # close instance
    await instance.close()


@pytest.mark.asyncio
async def test_schedule_refresh_replaces_result() -> None:
    """
    Test to check whether _schedule_refresh replaces a valid refresh result
    with another refresh result.
    """
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # check current refresh is valid
    assert await _is_valid(instance._current) is True
    current_refresh = instance._current
    # schedule new refresh
    await instance._schedule_refresh(0)
    new_refresh = instance._current
    # verify current has been replaced with new refresh
    assert current_refresh != new_refresh
    # check new refresh is valid
    assert await _is_valid(new_refresh) is True
    # close instance
    await instance.close()


@pytest.mark.asyncio
async def test_schedule_refresh_wont_replace_valid_result_with_invalid() -> None:
    """
    Test to check whether _schedule_refresh won't replace a valid
    refresh result with an invalid one.
    """
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # check current refresh is valid
    assert await _is_valid(instance._current) is True
    current_refresh = instance._current
    # set certificate to be expired
    client.instance.cert_before = datetime.now() - timedelta(minutes=20)
    client.instance.cert_expiry = datetime.now() - timedelta(minutes=10)
    # schedule new refresh
    new_refresh = instance._schedule_refresh(0)
    # check new refresh is invalid
    assert await _is_valid(new_refresh) is False
    # check current was not replaced
    assert current_refresh == instance._current
    # close instance
    await instance.close()


@pytest.mark.asyncio
async def test_schedule_refresh_expired_cert() -> None:
    """
    Test to check whether _schedule_refresh will throw RefreshError on
    expired certificate.
    """
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    # set certificate to be expired
    client.instance.cert_before = datetime.now() - timedelta(minutes=20)
    client.instance.cert_expiry = datetime.now() - timedelta(minutes=10)
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # check RefreshError is thrown
    with pytest.raises(RefreshError):
        await instance._current
    # close instance
    await instance.close()


@pytest.mark.asyncio
async def test_force_refresh_cancels_pending_refresh() -> None:
    """
    Test that force_refresh cancels pending task if refresh_in_progress event is not set.
    """
    keys = asyncio.create_task(generate_keys())
    client = FakeAlloyDBClient()
    instance = Instance(
        "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
        client,
        keys,
    )
    # make sure initial refresh is finished
    await instance._current
    # since the pending refresh isn't for another ~56 min, the refresh_in_progress event
    # shouldn't be set
    pending_refresh = instance._next
    assert instance._refresh_in_progress.is_set() is False
    await instance.force_refresh()
    # pending_refresh has to be awaited for it to raised as cancelled
    with pytest.raises(asyncio.CancelledError):
        assert await pending_refresh
    # verify pending_refresh has now been cancelled
    assert pending_refresh.cancelled() is True
    assert isinstance(await instance._current, RefreshResult)
    # close instance
    await instance.close()
