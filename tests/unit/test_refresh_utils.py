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

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from google.cloud.alloydb.connector.refresh_utils import _seconds_until_refresh


def test_seconds_until_refresh_over_1_hour() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is over 1 hour, should return duration/2.
    """
    now = datetime.now()
    assert _seconds_until_refresh(now + timedelta(minutes=62), now) == 31 * 60


def test_seconds_until_refresh_under_1_hour_over_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 1 hour and over 4 minutes,
    should return duration-refresh_buffer (refresh_buffer = 4 minutes).
    """
    now = datetime.now(timezone.utc)
    assert _seconds_until_refresh(now + timedelta(minutes=5), now) == 60


def test_seconds_until_refresh_under_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 4 minutes, should return 0.
    """
    assert (
        _seconds_until_refresh(datetime.now(timezone.utc) + timedelta(minutes=3)) == 0
    )
