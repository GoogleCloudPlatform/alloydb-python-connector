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

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter

from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import REFRESH_AHEAD_TYPE
from google.cloud.alloydbconnector.telemetry import REFRESH_SUCCESS
from google.cloud.alloydbconnector.telemetry import SHUTDOWN_TIMEOUT_MS
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import NullTelemetryProvider
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.telemetry import _auth_type_value
from google.cloud.alloydbconnector.telemetry import _system_metrics_exporter_class
from google.cloud.alloydbconnector.telemetry import _TelemetryProvider
from google.cloud.alloydbconnector.telemetry import new_telemetry_provider


@contextmanager
def _captured_logs(name: str) -> Iterator[list[logging.LogRecord]]:
    """Capture the records emitted on one logger.

    google-api-core turns off propagation on the "google" logger as soon as
    any Google client is built, so pytest's caplog fixture, which listens on
    the root logger, never sees this library's own records.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(name)
    handler = _Collector()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


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


class TestTelemetryProviderExport:
    """End-to-end checks on what the exporter puts on the wire.

    These drive a real _TelemetryProvider against a fake monitoring client so
    that the metric shape, and the RPC used to write it, are asserted rather
    than assumed.
    """

    @staticmethod
    def _provider_and_client() -> tuple[Any, MagicMock, list]:
        writes: list = []
        client = MagicMock()
        client.common_project_path.return_value = "projects/test-project"
        client.create_time_series.side_effect = lambda req, **kw: writes.append(
            ("create_time_series", req, kw)
        )
        client.create_service_time_series.side_effect = lambda req, **kw: writes.append(
            ("create_service_time_series", req, kw)
        )
        provider = _TelemetryProvider(
            project_id="test-project",
            client_uid="uid-1",
            version="1.0.0",
            monitoring_client=client,
        )
        return provider, client, writes

    def test_writes_with_create_service_time_series(self) -> None:
        """System metrics live under a Google-owned prefix, which
        CreateTimeSeries rejects. Matches WithCreateServiceTimeSeries() in the
        Go connector."""
        provider, client, writes = self._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider.shutdown()

        assert [name for name, _, _ in writes] == ["create_service_time_series"]
        assert not client.create_time_series.called

    def test_does_not_create_metric_descriptors(self) -> None:
        """Service metrics have server-side descriptors; creating them would
        fail and cost an RPC per metric."""
        provider, client, _ = self._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider.shutdown()

        assert not client.create_metric_descriptor.called

    def test_instance_labels_move_to_monitored_resource(self) -> None:
        """All instances share one MeterProvider, so instance identity arrives
        as metric labels and the exporter must relocate it to the resource."""
        provider, _, writes = self._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(
            TelemetryAttributes(
                iam_authn=True, cache_hit=True, dial_status=DIAL_SUCCESS
            )
        )
        provider.shutdown()

        ((_, request, _),) = writes
        (series,) = request.time_series
        assert series.metric.type == (
            "alloydb.googleapis.com/client/connector/dial_count"
        )
        assert series.resource.type == "alloydb.googleapis.com/InstanceClient"
        assert dict(series.resource.labels) == {
            "project_id": "my-proj",
            "location": "us-central1",
            "cluster_id": "my-cluster",
            "instance_id": "my-inst",
            "client_uid": "uid-1",
        }
        # Resource labels must not linger as metric labels.
        assert dict(series.metric.labels) == {
            "connector_type": "python",
            "auth_type": "iam",
            "is_cache_hit": "true",
            "status": DIAL_SUCCESS,
        }

    def test_each_instance_gets_its_own_time_series(self) -> None:
        provider, _, writes = self._provider_and_client()
        for name in ("inst-a", "inst-b"):
            mr = provider.create_metric_recorder(
                "my-proj", "us-central1", "my-cluster", name
            )
            mr.record_bytes_tx(100)
        provider.shutdown()

        ((_, request, _),) = writes
        instances = sorted(
            ts.resource.labels["instance_id"] for ts in request.time_series
        )
        assert instances == ["inst-a", "inst-b"]

    def test_export_failure_is_swallowed(self) -> None:
        """A monitoring outage or a missing IAM permission must not propagate
        out of the background export thread."""
        provider, client, _ = self._provider_and_client()
        client.create_service_time_series.side_effect = Exception("permission denied")
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider.shutdown()  # must not raise

    def test_write_failure_is_reported_on_this_library_s_logger(self) -> None:
        """The upstream exporter reports write failures with logger.error and
        a stack trace on a logger this library does not own. A missing IAM
        permission would otherwise produce an ERROR every export interval for
        the life of the process."""
        provider, client, _ = self._provider_and_client()
        client.create_service_time_series.side_effect = Exception("permission denied")
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        with _captured_logs("google.cloud.alloydbconnector.telemetry") as ours:
            with _captured_logs("opentelemetry.exporter.cloud_monitoring") as theirs:
                provider.shutdown()

        assert [r.levelname for r in theirs] == []
        assert [r for r in ours if "permission denied" in r.getMessage()], (
            "the write failure was not reported on this library's logger"
        )

    def test_conversion_failure_is_swallowed(self) -> None:
        """Failures upstream does not catch itself must not escape the
        background export thread either."""
        provider, _, _ = self._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        with patch.object(
            CloudMonitoringMetricsExporter,
            "export",
            side_effect=Exception("cannot convert data point"),
        ):
            with _captured_logs("google.cloud.alloydbconnector.telemetry") as ours:
                provider.shutdown()  # must not raise

        assert [r for r in ours if "cannot convert data point" in r.getMessage()]

    def test_write_rpc_gets_a_deadline(self) -> None:
        """CreateServiceTimeSeries has no default deadline and the upstream
        exporter does not pass its export budget down, so a stalled write
        would block shutdown indefinitely."""
        provider, _, writes = self._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider.shutdown()

        ((_, _, kwargs),) = writes
        assert 0 < kwargs["timeout"] <= SHUTDOWN_TIMEOUT_MS / 1000

    def test_does_not_mutate_third_party_logger(self) -> None:
        """Squelching the exporter's own logger would hide export failures
        from every other user of that library in the process."""
        exporter_logger = logging.getLogger("opentelemetry.exporter.cloud_monitoring")
        before = exporter_logger.level
        # Pin a known starting level so the assertion does not depend on what
        # an earlier test in this process left behind.
        exporter_logger.setLevel(logging.NOTSET)
        try:
            provider, _, _ = self._provider_and_client()
            provider.shutdown()
            assert exporter_logger.level == logging.NOTSET
        finally:
            exporter_logger.setLevel(before)

    def test_shutdown_is_bounded(self) -> None:
        """close() bounds only the telemetry flush, so the final export must
        not use OTel's 30s default."""
        provider, _, _ = self._provider_and_client()
        with patch.object(provider._provider, "shutdown") as shutdown:
            provider.shutdown()
        ((_, kwargs),) = [shutdown.call_args]
        assert kwargs["timeout_millis"] <= 3_000

    def test_exporter_class_is_built_once(self) -> None:
        """The subclass is shared process-wide rather than rebuilt for every
        Connector."""
        assert _system_metrics_exporter_class() is _system_metrics_exporter_class()


class TestForeignMetricsAreNotExported:
    """The OTel SDK registers self-observability instruments on whichever
    MeterProvider owns the reader. They arrive at the exporter wearing this
    connector's alloydb.googleapis.com prefix and carrying none of the
    instance labels, and writing an undefined type under a Google-owned
    prefix fails the whole batch -- taking every real metric with it.
    """

    def test_sdk_self_metrics_are_dropped(self) -> None:
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))

        # The reader's collection-duration histogram is only populated by a
        # collection, so it first reaches the exporter on the one after it.
        provider._provider.force_flush()
        writes.clear()
        provider.shutdown()

        written = {
            ts.metric.type for _, request, _ in writes for ts in request.time_series
        }
        assert written, "expected the connector's own metrics to still be written"
        assert all(
            t.startswith("alloydb.googleapis.com/client/connector/") for t in written
        )
        assert not [t for t in written if "otel.sdk" in t], (
            f"an opentelemetry-sdk self-metric was exported: {sorted(written)}"
        )

    def test_every_written_series_has_instance_labels(self) -> None:
        """A dropped metric is one thing; one written with empty required
        resource labels is rejected by Cloud Monitoring."""
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider._provider.force_flush()
        writes.clear()
        provider.shutdown()

        for _, request, _ in writes:
            for ts in request.time_series:
                assert dict(ts.resource.labels) == {
                    "project_id": "my-proj",
                    "location": "us-central1",
                    "cluster_id": "my-cluster",
                    "instance_id": "my-inst",
                    "client_uid": "uid-1",
                }


class TestObservableByteCounters:
    """Byte counts are reported by observable counters read at collection
    time, rather than pushed on the socket hot path."""

    def test_totals_are_reported_at_collection(self) -> None:
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_bytes_rx(100)
        mr.record_bytes_rx(23)
        mr.record_bytes_tx(7)
        provider.shutdown()

        points = {
            ts.metric.type: ts.points[0].value.int64_value
            for _, request, _ in writes
            for ts in request.time_series
        }
        assert (
            points["alloydb.googleapis.com/client/connector/bytes_received_count"]
            == 123
        )
        assert points["alloydb.googleapis.com/client/connector/bytes_sent_count"] == 7

    def test_counters_are_cumulative_across_exports(self) -> None:
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_bytes_rx(100)
        provider._provider.force_flush()
        mr.record_bytes_rx(50)
        writes.clear()
        provider.shutdown()

        rx = [
            ts.points[0].value.int64_value
            for _, request, _ in writes
            for ts in request.time_series
            if ts.metric.type.endswith("bytes_received_count")
        ]
        assert rx == [150], "observable counters must report a running total"

    def test_no_bytes_produces_no_byte_series(self) -> None:
        """AsyncConnector never records bytes. Reporting a zero would create
        an all-zero time series per instance."""
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider.shutdown()

        written = {
            ts.metric.type for _, request, _ in writes for ts in request.time_series
        }
        assert not [t for t in written if "bytes_" in t]
