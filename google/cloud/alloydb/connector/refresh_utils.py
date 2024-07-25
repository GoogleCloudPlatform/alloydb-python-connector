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

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
import logging

logger = logging.getLogger(name=__name__)

# _refresh_buffer is the amount of time before a refresh's result expires
# that a new refresh operation begins.
_refresh_buffer: int = 4 * 60  # 4 minutes


def _seconds_until_refresh(expiration: datetime) -> int:
    """
    Calculates the duration to wait before starting the next refresh.
    Usually the duration will be half of the time until certificate
    expiration.

    Args:
        expiration (datetime.datetime): Time of certificate expiration.
    Returns:
        int: Time in seconds to wait before performing next refresh.
    """

    duration = int((expiration - datetime.now(timezone.utc)).total_seconds())

    # if certificate duration is less than 1 hour
    if duration < 3600:
        # something is wrong with certificate, refresh now
        if duration < _refresh_buffer:
            return 0
        # otherwise wait until 4 minutes before expiration for next refresh
        return duration - _refresh_buffer
    return duration // 2


async def _is_valid(task: asyncio.Task) -> bool:
    try:
        result = await task
        # valid if current time is before cert expiration
        if datetime.now(timezone.utc) < result.expiration:
            return True
    except Exception:
        # suppress any errors from task
        logger.debug("Current refresh result is invalid.")
    return False
