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

from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import REFRESH_AHEAD_TYPE
from google.cloud.alloydbconnector.telemetry import REFRESH_SUCCESS
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import NullTelemetryProvider
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.telemetry import _auth_type_value
from google.cloud.alloydbconnector.telemetry import new_telemetry_provider


class TestAuthTypeValue:
    def test_iam(self) -> None:
        assert _auth_type_value(True) == "iam"

    def test_builtin(self) -> None:
        assert _auth_type_value(False) == "built_in"


class TestTelemetryAttributes:
    def test_defaults(self) -> None:
        attrs = TelemetryAttributes()
        assert attrs.iam_authn is False
        assert attrs.cache_hit is False
        assert attrs.dial_status == ""
        assert attrs.refresh_status == ""
        assert attrs.refresh_type == ""

    def test_custom_values(self) -> None:
        attrs = TelemetryAttributes(
            iam_authn=True,
            cache_hit=True,
            dial_status=DIAL_SUCCESS,
            refresh_status=REFRESH_SUCCESS,
            refresh_type=REFRESH_AHEAD_TYPE,
        )
        assert attrs.iam_authn is True
        assert attrs.cache_hit is True
        assert attrs.dial_status == DIAL_SUCCESS
        assert attrs.refresh_status == REFRESH_SUCCESS
        assert attrs.refresh_type == REFRESH_AHEAD_TYPE


class TestNullMetricRecorder:
    """NullMetricRecorder should be a no-op for all methods."""

    def test_record_dial_count(self) -> None:
        NullMetricRecorder().record_dial_count(TelemetryAttributes())

    def test_record_dial_latency(self) -> None:
        NullMetricRecorder().record_dial_latency(100.0)

    def test_record_open_connection(self) -> None:
        NullMetricRecorder().record_open_connection(TelemetryAttributes())

    def test_record_closed_connection(self) -> None:
        NullMetricRecorder().record_closed_connection(TelemetryAttributes())

    def test_record_bytes_rx(self) -> None:
        NullMetricRecorder().record_bytes_rx(1024)

    def test_record_bytes_tx(self) -> None:
        NullMetricRecorder().record_bytes_tx(1024)

    def test_record_refresh_count(self) -> None:
        NullMetricRecorder().record_refresh_count(TelemetryAttributes())


class TestNullTelemetryProvider:
    def test_shutdown(self) -> None:
        NullTelemetryProvider().shutdown()

    def test_create_metric_recorder_returns_null(self) -> None:
        provider = NullTelemetryProvider()
        mr = provider.create_metric_recorder("proj", "loc", "clust", "inst")
        assert isinstance(mr, NullMetricRecorder)


class TestNewTelemetryProvider:
    def test_disabled_returns_null(self) -> None:
        provider = new_telemetry_provider(
            enabled=False,
            project_id="proj",
            client_uid="uid",
            version="1.0.0",
        )
        assert isinstance(provider, NullTelemetryProvider)

    def test_none_client_returns_null(self) -> None:
        provider = new_telemetry_provider(
            enabled=True,
            project_id="proj",
            client_uid="uid",
            version="1.0.0",
            monitoring_client=None,
        )
        assert isinstance(provider, NullTelemetryProvider)

    def test_invalid_client_returns_null(self) -> None:
        """If the exporter fails to initialize, return NullTelemetryProvider."""
        provider = new_telemetry_provider(
            enabled=True,
            project_id="proj",
            client_uid="uid",
            version="1.0.0",
            monitoring_client="not-a-real-client",
        )
        # Should gracefully fall back to NullTelemetryProvider
        assert isinstance(provider, NullTelemetryProvider)
