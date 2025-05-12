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


class AsyncRateLimiter(object):
    """
    An asyncio-compatible rate limiter.

    Uses the Token Bucket algorithm (https://en.wikipedia.org/wiki/Token_bucket)
    to limit the number of function calls over a time interval using an event queue.

    Args:
        max_capacity (int): The maximum capacity of tokens the bucket
            will store at any one time. Defaults to 1.

        rate (float): The number of tokens that should be added per second.
            Defaults to 1 / 60.
    """

    def __init__(
        self,
        max_capacity: int = 1,
        rate: float = 1 / 60,
    ) -> None:
        self._rate = rate
        self._max_capacity = max_capacity
        self._loop = asyncio.get_running_loop()
        self._tokens: float = max_capacity
        self._last_token_update = self._loop.time()
        self._lock = asyncio.Lock()

    def _update_token_count(self) -> None:
        """
        Calculates how much time has passed since the last leak and removes the
        appropriate amount of events from the queue.
        Leaking is done lazily, meaning that if there is a large time gap between
        leaks, the next set of calls might be a burst if burst_size > 1
        """
        now = self._loop.time()
        time_elapsed = now - self._last_token_update
        new_tokens = time_elapsed * self._rate
        self._tokens = min(new_tokens + self._tokens, self._max_capacity)
        self._last_token_update = now

    async def _wait_for_next_token(self) -> None:
        """
        Wait until enough time has elapsed to add another token.
        """
        token_deficit = 1 - self._tokens
        if token_deficit > 0:
            wait_time = token_deficit / self._rate
            await asyncio.sleep(wait_time)

    async def acquire(self) -> None:
        """
        Waits for a token to become available, if necessary, then subtracts token and allows
        request to go through.
        """
        async with self._lock:
            self._update_token_count()
            if self._tokens < 1:
                await self._wait_for_next_token()
                self._update_token_count()
            self._tokens -= 1
