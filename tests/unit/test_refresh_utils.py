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

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Sequence

from mocks import FakeCredentials
from mocks import FakeCredentialsRequiresScopes
import pytest

from google.auth.credentials import TokenState
import google.auth.external_account_authorized_user
from google.cloud.alloydbconnector.refresh_utils import _ALLOYDB_LOGIN_SCOPE
from google.cloud.alloydbconnector.refresh_utils import _downscope_credentials
from google.cloud.alloydbconnector.refresh_utils import _seconds_until_refresh
import google.oauth2.credentials


def test_seconds_until_refresh_over_1_hour() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is over 1 hour, should return duration/2.
    """
    # using pytest.approx since sometimes can be off by a second
    assert (
        pytest.approx(
            _seconds_until_refresh(datetime.now(timezone.utc) + timedelta(minutes=62)),
            1,
        )
        == 31 * 60
    )


def test_seconds_until_refresh_under_1_hour_over_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 1 hour and over 4 minutes,
    should return duration-refresh_buffer (refresh_buffer = 4 minutes).
    """
    # using pytest.approx since sometimes can be off by a second
    assert (
        pytest.approx(
            _seconds_until_refresh(datetime.now(timezone.utc) + timedelta(minutes=5)),
            1,
        )
        == 60
    )


def test_seconds_until_refresh_under_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 4 minutes, should return 0.
    """
    assert (
        _seconds_until_refresh(datetime.now(timezone.utc) + timedelta(minutes=3)) == 0
    )


@pytest.mark.parametrize(
    "creds",
    [
        FakeCredentialsRequiresScopes(),
        FakeCredentialsRequiresScopes().with_scopes(
            ["https://www.googleapis.com/auth/cloud-platform"]
        ),
    ],
)
def test_downscope_credentials_scoped(creds: FakeCredentialsRequiresScopes) -> None:
    """Test _downscope_credentials uses with_scopes() for Scoped credentials."""
    original_scopes = creds._scopes
    downscoped = _downscope_credentials(creds)
    assert downscoped is not creds
    assert downscoped._scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert creds._scopes == original_scopes


@pytest.mark.parametrize(
    "initial_scopes",
    [
        None,
        ["https://www.googleapis.com/auth/cloud-platform"],
    ],
)
def test_downscope_credentials_user_adc_refresh_token(
    initial_scopes: list[str] | None,
) -> None:
    """Test _downscope_credentials narrows scopes and clears cached token for
    refresh-token-backed user credentials without mutating the original."""
    future_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    creds = google.oauth2.credentials.Credentials(
        token="cloud-platform-token",
        refresh_token="1//test-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=initial_scopes,
        expiry=future_expiry,
    )

    downscoped = _downscope_credentials(creds)

    assert downscoped is not creds
    assert downscoped.scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert downscoped.token is None
    assert downscoped.expiry is None
    assert not downscoped.valid
    assert downscoped.token_state == TokenState.INVALID
    # Original credentials must remain untouched for Admin API calls.
    assert creds.scopes == initial_scopes
    assert creds.token == "cloud-platform-token"
    assert creds.expiry == future_expiry


def test_downscope_credentials_user_refresh_handler() -> None:
    """Test _downscope_credentials narrows scopes and clears cached token when
    a refresh_handler is configured."""
    future_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    seen_scopes: list[Sequence[str]] = []

    def handler(_request: object, scopes: Sequence[str]) -> tuple[str, datetime]:
        seen_scopes.append(scopes)
        return ("downscoped-token", future_expiry)

    creds = google.oauth2.credentials.Credentials(
        token="cloud-platform-token",
        refresh_handler=handler,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
        expiry=future_expiry,
    )

    downscoped = _downscope_credentials(creds)

    assert downscoped is not creds
    assert downscoped.scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert downscoped.token is None
    assert downscoped.expiry is None
    assert creds.token == "cloud-platform-token"
    assert creds.scopes == ["https://www.googleapis.com/auth/cloud-platform"]

    downscoped.refresh(object())
    assert seen_scopes == [[_ALLOYDB_LOGIN_SCOPE]]
    assert downscoped.token == "downscoped-token"


def test_downscope_credentials_user_token_only_preserves_token() -> None:
    """Test _downscope_credentials preserves pre-minted tokens when the user
    credentials cannot be refreshed, avoiding RefreshError on connect."""
    future_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    creds = google.oauth2.credentials.Credentials(
        token="pre-minted-token",
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
        expiry=future_expiry,
    )

    downscoped = _downscope_credentials(creds)

    assert downscoped is not creds
    assert downscoped.scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert downscoped.token == "pre-minted-token"
    assert downscoped.expiry == future_expiry
    assert downscoped.valid
    assert downscoped.token_state == TokenState.FRESH
    assert creds.scopes == ["https://www.googleapis.com/auth/cloud-platform"]


def test_downscope_credentials_external_account_authorized_user() -> None:
    """Test _downscope_credentials respects can_refresh on
    external_account_authorized_user.Credentials."""
    future_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    refreshable = google.auth.external_account_authorized_user.Credentials(
        token="initial-token",
        expiry=future_expiry,
        refresh_token="refresh-token",
        token_url="https://sts.googleapis.com/v1/oauthtoken",
        client_id="client-id",
        client_secret="client-secret",
    )
    downscoped_refreshable = _downscope_credentials(refreshable)
    assert downscoped_refreshable is not refreshable
    assert downscoped_refreshable.scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert downscoped_refreshable.token is None
    assert downscoped_refreshable.expiry is None
    assert refreshable.token == "initial-token"

    token_only = google.auth.external_account_authorized_user.Credentials(
        token="pre-minted-token",
        expiry=future_expiry,
    )
    downscoped_token_only = _downscope_credentials(token_only)
    assert downscoped_token_only is not token_only
    assert downscoped_token_only.scopes == [_ALLOYDB_LOGIN_SCOPE]
    assert downscoped_token_only.token == "pre-minted-token"
    assert downscoped_token_only.expiry == future_expiry


def test_downscope_credentials_custom_scopes() -> None:
    """Test _downscope_credentials uses explicit scopes when provided."""
    custom_scopes = ["https://www.googleapis.com/auth/custom.scope"]
    creds = FakeCredentialsRequiresScopes()
    downscoped = _downscope_credentials(creds, scopes=custom_scopes)
    custom_scopes.append("https://www.googleapis.com/auth/mutated")
    assert downscoped._scopes == ["https://www.googleapis.com/auth/custom.scope"]


def test_downscope_credentials_unscoped_passthrough() -> None:
    """Test _downscope_credentials returns non-ReadOnlyScoped credentials as-is."""
    creds = FakeCredentials()
    assert _downscope_credentials(creds) is creds
