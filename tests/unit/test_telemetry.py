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
import threading
import time
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch
import warnings

from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
import pytest

from google.cloud.alloydbconnector.telemetry import _EXPORT_TIMEOUT_MS
from google.cloud.alloydbconnector.telemetry import _EXPORTER_ATTRS
from google.cloud.alloydbconnector.telemetry import DIAL_SUCCESS
from google.cloud.alloydbconnector.telemetry import REFRESH_AHEAD_TYPE
from google.cloud.alloydbconnector.telemetry import REFRESH_SUCCESS
from google.cloud.alloydbconnector.telemetry import SHUTDOWN_TIMEOUT_MS
from google.cloud.alloydbconnector.telemetry import TELEMETRY_SHUTDOWN_TIMEOUT_S
from google.cloud.alloydbconnector.telemetry import NullMetricRecorder
from google.cloud.alloydbconnector.telemetry import NullTelemetryProvider
from google.cloud.alloydbconnector.telemetry import TelemetryAttributes
from google.cloud.alloydbconnector.telemetry import _auth_type_value
from google.cloud.alloydbconnector.telemetry import _Instruments
from google.cloud.alloydbconnector.telemetry import _system_metrics_exporter_class
from google.cloud.alloydbconnector.telemetry import _TelemetryMixin
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

    def test_every_write_is_capped_not_only_the_last_one(self) -> None:
        """MeterProvider registers an atexit handler that calls shutdown()
        with OTel's 30s default, and the reader hands the export interval --
        not the shutdown deadline -- to that final export. A connector that
        is never closed must still not sit in a 60s write at exit."""
        provider, _, writes = self._provider_and_client()
        assert provider._exporter._max_write_timeout_s == _EXPORT_TIMEOUT_MS / 1000

        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        # Go through MeterProvider.shutdown directly, as the atexit handler
        # does, rather than through _TelemetryProvider.shutdown.
        provider._provider.shutdown(timeout_millis=30_000)

        ((_, _, kwargs),) = writes
        assert 0 < kwargs["timeout"] <= _EXPORT_TIMEOUT_MS / 1000

    def test_one_deadline_covers_every_chunk(self) -> None:
        """Cloud Monitoring takes at most MAX_BATCH_WRITE series per call, so
        a large project writes in several. Handing each the full budget would
        let a flush bounded by SHUTDOWN_TIMEOUT_MS take a multiple of it."""
        provider, client, writes = self._provider_and_client()
        for i in range(205):
            mr = provider.create_metric_recorder(
                "my-proj", "us-central1", "my-cluster", f"inst-{i}"
            )
            mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))

        def slow_write(request: Any, **kwargs: Any) -> None:
            time.sleep(0.2)
            writes.append(("create_service_time_series", request, kwargs))

        client.create_service_time_series.side_effect = slow_write
        provider.shutdown()

        timeouts = [kwargs["timeout"] for _, _, kwargs in writes]
        assert len(timeouts) > 1, "expected more than one chunked write"
        # Strictly decreasing: each chunk gets what is *left* of the one
        # budget. Equal timeouts would mean every chunk got a fresh copy of
        # it, which is the bug this guards.
        assert all(later < earlier for earlier, later in zip(timeouts, timeouts[1:])), (
            timeouts
        )
        assert max(timeouts) <= SHUTDOWN_TIMEOUT_MS / 1000

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

    @staticmethod
    def _provider_with_sdk_self_metrics(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[Any, list, list]:
        """A provider whose reader also reports its own SDK metrics.

        The SDK reads OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED when the
        reader is constructed, and it is off by default. Without it these
        tests pass whether or not the exporter filters anything, because no
        foreign metric is ever produced.
        """
        monkeypatch.setenv("OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED", "true")
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        # What the exporter was handed, before it dropped anything.
        collected: list[str] = []
        exporter = provider._exporter
        inner = exporter.export

        def spy(metrics_data: Any, *args: Any, **kwargs: Any) -> Any:
            collected.extend(
                metric.name
                for rm in metrics_data.resource_metrics
                for sm in rm.scope_metrics
                for metric in sm.metrics
            )
            return inner(metrics_data, *args, **kwargs)

        exporter.export = spy  # type: ignore[method-assign]
        return provider, writes, collected

    def test_sdk_self_metrics_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, writes, collected = self._provider_with_sdk_self_metrics(monkeypatch)
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))

        # The reader's collection-duration histogram is only populated by a
        # collection, so it first reaches the exporter on the one after it.
        provider._provider.force_flush()
        writes.clear()
        collected.clear()
        provider.shutdown()

        assert [n for n in collected if n.startswith("otel.sdk.")], (
            "no SDK self-metric reached the exporter, so this test would "
            f"pass without any filtering at all: {sorted(set(collected))}"
        )
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

    def test_every_written_series_has_instance_labels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dropped metric is one thing; one written with empty required
        resource labels is rejected by Cloud Monitoring."""
        provider, writes, collected = self._provider_with_sdk_self_metrics(monkeypatch)
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider._provider.force_flush()
        writes.clear()
        collected.clear()
        provider.shutdown()

        assert [n for n in collected if n.startswith("otel.sdk.")], (
            "no SDK self-metric reached the exporter, so this test would "
            f"pass without any filtering at all: {sorted(set(collected))}"
        )
        for _, request, _ in writes:
            for ts in request.time_series:
                assert dict(ts.resource.labels) == {
                    "project_id": "my-proj",
                    "location": "us-central1",
                    "cluster_id": "my-cluster",
                    "instance_id": "my-inst",
                    "client_uid": "uid-1",
                }

    def test_a_foreign_gauge_does_not_sink_the_export(self) -> None:
        """Foreign metrics must be dropped before upstream converts their
        data points, not after.

        Upstream builds a point's time interval from the metric kind
        _get_metric_descriptor returns, and that method can only guess at a
        kind it does not define. Guessing CUMULATIVE for a gauge -- whose
        data points carry no start time -- raises during the conversion,
        which fails the entire export and takes every real metric with it.
        """
        provider, _, writes = TestTelemetryProviderExport._provider_and_client()
        mr = provider.create_metric_recorder(
            "my-proj", "us-central1", "my-cluster", "my-inst"
        )
        mr.record_dial_count(TelemetryAttributes(dial_status=DIAL_SUCCESS))
        provider._provider.get_meter("someone-else").create_gauge("foreign").set(5)
        provider.shutdown()

        written = {
            ts.metric.type for _, request, _ in writes for ts in request.time_series
        }
        assert written == {"alloydb.googleapis.com/client/connector/dial_count"}


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


class TestExporterCompatibilityGuard:
    """The exporter subclass overrides upstream private hooks and reads
    upstream private state. If a release renames any of it, the overrides
    stop doing their job silently -- so fail closed to
    NullTelemetryProvider instead."""

    def test_missing_hook_falls_back_to_null(self) -> None:
        """An upstream release that renamed _batch_write would leave the
        override uncalled and every metric written with CreateTimeSeries
        under a prefix that rejects it."""
        _system_metrics_exporter_class.cache_clear()
        try:
            with patch(
                "opentelemetry.exporter.cloud_monitoring"
                ".CloudMonitoringMetricsExporter",
                _RenamedHookExporter,
            ):
                provider = new_telemetry_provider(
                    enabled=True,
                    project_id="proj",
                    client_uid="uid",
                    version="1.0.0",
                    monitoring_client=MagicMock(),
                )
            assert isinstance(provider, NullTelemetryProvider)
        finally:
            # The class is cached process-wide; do not leave the stand-in
            # behind for the tests that follow.
            _system_metrics_exporter_class.cache_clear()

    def test_missing_instance_attribute_falls_back_to_null(self) -> None:
        """_prefix and friends are only set in upstream's __init__, so the
        class-level hook check cannot see them."""
        client = MagicMock()
        client.common_project_path.return_value = "projects/proj"
        with patch(
            "google.cloud.alloydbconnector.telemetry._EXPORTER_ATTRS",
            ("_prefix", "a_renamed_attribute"),
        ):
            provider = new_telemetry_provider(
                enabled=True,
                project_id="proj",
                client_uid="uid",
                version="1.0.0",
                monitoring_client=client,
            )
        assert isinstance(provider, NullTelemetryProvider)

    def test_the_guarded_attributes_exist_on_the_installed_exporter(self) -> None:
        """The guard is only worth having if it names real attributes: a
        typo in _EXPORTER_ATTRS would fail every provider closed."""
        client = MagicMock()
        client.common_project_path.return_value = "projects/proj"
        exporter = _system_metrics_exporter_class()(
            project_id="proj", client=client, prefix="alloydb.googleapis.com"
        )
        for attr in _EXPORTER_ATTRS:
            assert hasattr(exporter, attr), attr

    def test_the_installed_exporter_is_supported(self) -> None:
        """The CI canary for this dependency.

        pyproject.toml puts no upper bound on the exporter, so every CI run
        resolves to whatever the newest release is. This is what turns a
        breaking one into a red build: it fails if the hooks, the private
        instance state, or the upstream deprecation stop lining up, instead of
        leaving users to notice that metrics quietly stopped arriving.
        """
        client = MagicMock()
        client.common_project_path.return_value = "projects/proj"
        # Clear the cache so the subclass is rebuilt here, inside the window
        # the assertions cover, rather than reused from an earlier test.
        _system_metrics_exporter_class.cache_clear()
        try:
            with _captured_logs("google.cloud.alloydbconnector.telemetry") as ours:
                provider = new_telemetry_provider(
                    enabled=True,
                    project_id="proj",
                    client_uid="uid",
                    version="1.0.0",
                    monitoring_client=client,
                )
            assert isinstance(provider, _TelemetryProvider), (
                "the installed opentelemetry-exporter-gcp-monitoring is not "
                "usable by this connector: " + "; ".join(r.getMessage() for r in ours)
            )
            assert [r.getMessage() for r in ours if r.levelno >= logging.WARNING] == []
            provider.shutdown()
        finally:
            _system_metrics_exporter_class.cache_clear()

    def test_upstream_deprecation_does_not_reach_consumers(self) -> None:
        """Upstream marks the exporter @deprecated as of 1.13 and points at
        the OTLP exporter, which cannot write a Google-owned prefix at all.
        The warning is not actionable downstream, and under -W error it would
        abort the exporter and disable metrics outright."""
        client = MagicMock()
        client.common_project_path.return_value = "projects/proj"
        # Clear the cache so that subclassing the deprecated exporter, which
        # warns on its own, happens inside the assertion window too.
        _system_metrics_exporter_class.cache_clear()
        try:
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("error", DeprecationWarning)
                provider = new_telemetry_provider(
                    enabled=True,
                    project_id="proj",
                    client_uid="uid",
                    version="1.0.0",
                    monitoring_client=client,
                )
            assert isinstance(provider, _TelemetryProvider)
            assert [str(w.message) for w in seen] == []
            provider.shutdown()
        finally:
            _system_metrics_exporter_class.cache_clear()

    def test_failed_init_does_not_leak_the_export_thread(self) -> None:
        """Constructing the reader starts a thread. A provider that fails
        to build afterwards is discarded, so nothing would ever shut that
        thread down."""
        client = MagicMock()
        client.common_project_path.return_value = "projects/proj"
        before = {t.name for t in threading.enumerate()}

        with patch.object(_Instruments, "__init__", side_effect=RuntimeError("boom")):
            provider = new_telemetry_provider(
                enabled=True,
                project_id="proj",
                client_uid="uid",
                version="1.0.0",
                monitoring_client=client,
            )

        assert isinstance(provider, NullTelemetryProvider)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            leaked = [
                t
                for t in threading.enumerate()
                if t.name not in before and "PeriodicExportingMetricReader" in t.name
            ]
            if not leaked:
                break
            time.sleep(0.01)
        assert not leaked, (
            f"the reader's export thread outlived the failed init: {leaked}"
        )


class _RenamedHookExporter:
    """Stands in for an upstream release that renamed a private hook."""

    def __init__(self, *args, **kwargs) -> None:
        pass


class TestTelemetryShutdown:
    """_shutdown_telemetry bounds how long close() waits on the final
    metric export, for any number of projects."""

    class _Mixin(_TelemetryMixin):
        pass

    @staticmethod
    def _mixin(providers: dict, monitoring_client: Any = None) -> Any:
        mixin = TestTelemetryShutdown._Mixin()
        mixin._init_telemetry(False, None)
        mixin._telemetry_providers = providers
        mixin._monitoring_client = monitoring_client
        return mixin

    async def test_providers_are_flushed_concurrently(self) -> None:
        """Each provider gets the full SHUTDOWN_TIMEOUT_MS for its own
        write. Flushed in sequence, two projects would overrun the wait and
        the second would never be flushed at all."""

        class SlowProvider:
            def __init__(self) -> None:
                self.flushed = False

            def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
                time.sleep(SHUTDOWN_TIMEOUT_MS / 1000)
                self.flushed = True

        a, b, c = SlowProvider(), SlowProvider(), SlowProvider()
        mixin = self._mixin({"a": a, "b": b, "c": c})

        start = time.monotonic()
        await mixin._shutdown_telemetry()
        elapsed = time.monotonic() - start

        assert [a.flushed, b.flushed, c.flushed] == [True, True, True]
        assert elapsed < TELEMETRY_SHUTDOWN_TIMEOUT_S

    async def test_abandoned_flush_keeps_the_monitoring_client_open(self) -> None:
        """Closing the channel under an in-flight write would fail the very
        write the deadline was meant to let finish."""
        release = threading.Event()

        class HangingProvider:
            def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
                release.wait(30)

        client = MagicMock()
        mixin = self._mixin({"a": HangingProvider()}, monitoring_client=client)
        try:
            with patch(
                "google.cloud.alloydbconnector.telemetry.TELEMETRY_SHUTDOWN_TIMEOUT_S",
                0.2,
            ):
                await mixin._shutdown_telemetry()
            assert not client.close.called
            assert mixin._monitoring_client is None
        finally:
            release.set()

    async def test_one_failure_does_not_abandon_the_other_providers(self) -> None:
        """A bare gather propagates the first failure immediately, leaving
        the remaining flushes running on executor threads with nothing
        waiting on them. A provider that raised has also finished, so it must
        not keep the channel open either."""

        class FailingProvider:
            def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
                raise RuntimeError("reader failed during shutdown")

        class RecordingProvider:
            def __init__(self) -> None:
                self.flushed = False

            def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
                self.flushed = True

        other = RecordingProvider()
        client = MagicMock()
        mixin = self._mixin(
            {"a": FailingProvider(), "b": other}, monitoring_client=client
        )
        with _captured_logs("google.cloud.alloydbconnector.telemetry") as ours:
            await mixin._shutdown_telemetry()

        assert other.flushed
        assert client.close.called
        assert [r for r in ours if "reader failed during shutdown" in r.getMessage()]

    async def test_client_is_released_once_the_flush_lands(self) -> None:
        class FastProvider:
            def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
                pass

        client = MagicMock()
        mixin = self._mixin({"a": FastProvider()}, monitoring_client=client)
        await mixin._shutdown_telemetry()

        assert client.close.called
        assert mixin._monitoring_client is None
        assert mixin._telemetry_providers == {}
