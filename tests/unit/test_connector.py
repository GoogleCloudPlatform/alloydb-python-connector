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
from threading import Thread
from typing import Union

from mock import patch
from mocks import FakeAlloyDBClient
from mocks import FakeCredentials
import pytest

from google.cloud.alloydb.connector import Connector
from google.cloud.alloydb.connector import IPTypes


def test_Connector_init(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets default attributes.
    """
    connector = Connector(credentials)
    assert connector._quota_project is None
    assert connector._alloydb_api_endpoint == "https://alloydb.googleapis.com"
    assert connector._client is None
    assert connector._credentials == credentials
    connector.close()


def test_Connector_init_bad_ip_type(credentials: FakeCredentials) -> None:
    """Test that Connector errors due to bad ip_type str."""
    bad_ip_type = "BAD-IP-TYPE"
    with pytest.raises(ValueError) as exc_info:
        Connector(ip_type=bad_ip_type, credentials=credentials)
    assert (
        exc_info.value.args[0]
        == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE'."
    )


@pytest.mark.parametrize(
    "ip_type, expected",
    [
        (
            "private",
            IPTypes.PRIVATE,
        ),
        (
            "PRIVATE",
            IPTypes.PRIVATE,
        ),
        (
            IPTypes.PRIVATE,
            IPTypes.PRIVATE,
        ),
        (
            "public",
            IPTypes.PUBLIC,
        ),
        (
            "PUBLIC",
            IPTypes.PUBLIC,
        ),
        (
            IPTypes.PUBLIC,
            IPTypes.PUBLIC,
        ),
    ],
)
def test_Connector_init_ip_type(
    ip_type: Union[str, IPTypes], expected: IPTypes, credentials: FakeCredentials
) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets ip_type.
    """
    connector = Connector(credentials=credentials, ip_type=ip_type)
    assert connector._ip_type == expected
    connector.close()


def test_Connector_context_manager(credentials: FakeCredentials) -> None:
    """
    Test to check whether the __init__ method of Connector
    properly sets defaults as context manager.
    """
    with Connector(credentials) as connector:
        assert connector._quota_project is None
        assert connector._alloydb_api_endpoint == "https://alloydb.googleapis.com"
        assert connector._client is None
        assert connector._credentials == credentials


def test_Connector_close(credentials: FakeCredentials) -> None:
    """
    Test that Connector's close method stops event loop and
    background thread.
    """
    with Connector(credentials) as connector:
        loop: asyncio.AbstractEventLoop = connector._loop
        thread: Thread = connector._thread
        assert loop.is_running() is True
        assert thread.is_alive() is True
    assert loop.is_running() is False
    assert thread.is_alive() is False


@pytest.mark.usefixtures("proxy_server")
def test_connect(credentials: FakeCredentials, fake_client: FakeAlloyDBClient) -> None:
    """
    Test that connector.connect returns connection object.
    """
    client = fake_client
    with Connector(credentials) as connector:
        connector._client = client
        # patch db connection creation
        with patch("google.cloud.alloydb.connector.pg8000.connect") as mock_connect:
            mock_connect.return_value = True
            connection = connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
            )
        # check connection is returned
        assert connection is True


def test_connect_bad_ip_type(
    credentials: FakeCredentials, fake_client: FakeAlloyDBClient
) -> None:
    """Test that Connector.connect errors due to bad ip_type str."""
    with Connector(credentials=credentials) as connector:
        connector._client = fake_client
        bad_ip_type = "BAD-IP-TYPE"
        with pytest.raises(ValueError) as exc_info:
            connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "pg8000",
                user="test-user",
                password="test-password",
                db="test-db",
                ip_type=bad_ip_type,
            )
        assert (
            exc_info.value.args[0]
            == f"Incorrect value for ip_type, got '{bad_ip_type}'. Want one of: 'PUBLIC', 'PRIVATE'."
        )


def test_connect_unsupported_driver(credentials: FakeCredentials) -> None:
    """
    Test that connector.connect errors with unsupported database driver.
    """
    client = FakeAlloyDBClient()
    with Connector(credentials) as connector:
        connector._client = client
        # try to connect using unsupported driver, should raise ValueError
        with pytest.raises(ValueError) as exc_info:
            connector.connect(
                "projects/test-project/locations/test-region/clusters/test-cluster/instances/test-instance",
                "bad_driver",
            )
        # assert custom error message for unsupported driver is present
        assert (
            exc_info.value.args[0]
            == "Driver 'bad_driver' is not a supported database driver."
        )


def test_Connector_close_called_multiple_times(credentials: FakeCredentials) -> None:
    """Test that Connector.close can be called multiple times."""
    # open and close Connector object
    connector = Connector(credentials=credentials)
    # verify background thread exists
    assert connector._thread
    connector.close()
    # check that connector thread is no longer running
    assert connector._thread.is_alive() is False
    # call connector.close a second time
    connector.close()
