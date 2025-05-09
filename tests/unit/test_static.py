# Copyright 2025 Google LLC
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

from mocks import FakeInstance
from mocks import write_static_info

from google.cloud.alloydbconnector.connection_info import ConnectionInfo
from google.cloud.alloydbconnector.static import StaticConnectionInfoCache


def test_StaticConnectionInfoCache_init() -> None:
    """
    Test that StaticConnectionInfoCache.__init__ populates its ConnectionInfo
    object.
    """
    i = FakeInstance()
    static_info = write_static_info(i)
    cache = StaticConnectionInfoCache(i.uri(), static_info)
    assert len(cache._info.cert_chain) == 3
    assert cache._info.ca_cert
    assert cache._info.key
    assert cache._info.ip_addrs == {
        "PRIVATE": i.ip_addrs["PRIVATE"],
        "PUBLIC": i.ip_addrs["PUBLIC"],
        "PSC": i.ip_addrs["PSC"],
    }
    assert cache._info.expiration


def test_StaticConnectionInfoCache_init_trailing_dot_dns() -> None:
    """
    Test that StaticConnectionInfoCache.__init__ populates its ConnectionInfo
    object correctly when its PSC DNS name contains a trailing dot.
    """
    i = FakeInstance()
    no_trailing_dot_dns = i.ip_addrs["PSC"]
    i.ip_addrs["PSC"] += "."
    static_info = write_static_info(i)
    cache = StaticConnectionInfoCache(i.uri(), static_info)
    assert len(cache._info.cert_chain) == 3
    assert cache._info.ca_cert
    assert cache._info.key
    assert cache._info.ip_addrs == {
        "PRIVATE": i.ip_addrs["PRIVATE"],
        "PUBLIC": i.ip_addrs["PUBLIC"],
        "PSC": no_trailing_dot_dns,
    }
    assert cache._info.expiration


async def test_StaticConnectionInfoCache_force_refresh() -> None:
    """
    Test that StaticConnectionInfoCache.force_refresh is a no-op.
    """
    i = FakeInstance()
    static_info = write_static_info(i)
    cache = StaticConnectionInfoCache(i.uri(), static_info)
    conn_info = cache._info
    await cache.force_refresh()
    conn_info2 = cache._info
    assert conn_info2 == conn_info


async def test_StaticConnectionInfoCache_connect_info() -> None:
    """
    Test that StaticConnectionInfoCache.connect_info returns the ConnectionInfo
    object.
    """
    i = FakeInstance()
    static_info = write_static_info(i)
    cache = StaticConnectionInfoCache(i.uri(), static_info)
    # check that cached connection info is now set
    assert isinstance(cache._info, ConnectionInfo)
    conn_info = cache._info
    # check that calling connect_info uses cached info
    conn_info2 = await cache.connect_info()
    assert conn_info2 == conn_info


async def test_StaticConnectionInfoCache_close() -> None:
    """
    Test that StaticConnectionInfoCache.close is a no-op.
    """
    i = FakeInstance()
    static_info = write_static_info(i)
    cache = StaticConnectionInfoCache(i.uri(), static_info)
    conn_info = cache._info
    await cache.close()
    conn_info2 = cache._info
    assert conn_info2 == conn_info
