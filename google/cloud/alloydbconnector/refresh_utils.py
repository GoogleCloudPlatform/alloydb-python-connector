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
from collections.abc import Sequence
import copy
from datetime import datetime
from datetime import timezone
import logging
from typing import TYPE_CHECKING
from typing import Optional

from google.auth.credentials import ReadOnlyScoped
from google.auth.credentials import Scoped

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

logger = logging.getLogger(name=__name__)

# _refresh_buffer is the amount of time before a refresh's result expires
# that a new refresh operation begins.
_refresh_buffer: int = 4 * 60  # 4 minutes
_ALLOYDB_LOGIN_SCOPE = "https://www.googleapis.com/auth/alloydb.login"


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


def _is_refreshable(credentials: Credentials) -> bool:
    """Return whether a non-Scoped credential can refresh its access token."""
    can_refresh = getattr(credentials, "can_refresh", None)
    if isinstance(can_refresh, bool):
        return can_refresh
    return (
        bool(getattr(credentials, "_refresh_token", None))
        or getattr(credentials, "refresh_handler", None) is not None
    )


def _downscope_credentials(
    credentials: Credentials,
    scopes: Optional[Sequence[str]] = None,
) -> Credentials:
    """Generate a down-scoped credential.

    Args:
        credentials (google.auth.credentials.Credentials):
            Credentials object used to generate down-scoped credentials.
        scopes (Optional[Sequence[str]]): List of Google scopes to
            include in down-scoped credentials object. Defaults to the
            AlloyDB login scope.

    Returns:
        google.auth.credentials.Credentials: Down-scoped credentials object.
    """
    target_scopes = [_ALLOYDB_LOGIN_SCOPE] if scopes is None else list(scopes)
    # Credentials sourced from a service account, GCE metadata, impersonation,
    # or workload identity inherit from Scoped and support with_scopes().
    if isinstance(credentials, Scoped):
        return credentials.with_scopes(scopes=target_scopes)
    # Authenticated user credentials (e.g. gcloud ADC,
    # google.oauth2.credentials.Credentials) inherit from ReadOnlyScoped rather
    # than Scoped. Shallow-copy so the Admin API / caller credentials are not
    # mutated, narrow _scopes, and clear any cached token when refreshable so
    # the next connection fetches a down-scoped token.
    if isinstance(credentials, ReadOnlyScoped):
        scoped_creds = copy.copy(credentials)
        # google.oauth2.credentials.Credentials.__getstate__ strips
        # _refresh_handler for pickling, so copy.copy() drops it; repopulate
        # via the public setter when present.
        refresh_handler = getattr(credentials, "refresh_handler", None)
        if refresh_handler is not None:
            setattr(scoped_creds, "refresh_handler", refresh_handler)
        scoped_creds._scopes = target_scopes
        if _is_refreshable(credentials):
            scoped_creds.token = None
            scoped_creds.expiry = None
        return scoped_creds
    return credentials
