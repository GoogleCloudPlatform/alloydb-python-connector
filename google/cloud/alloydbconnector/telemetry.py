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

"""Built-in telemetry for the AlloyDB Python Connector.

This module provides internal metrics collection using OpenTelemetry with a
Cloud Monitoring exporter. Metrics are exported to the
alloydb.googleapis.com/client/connector system metric prefix. Presently,
these metrics aren't public, but will be made public in the future.

The telemetry is enabled by default and can be disabled via the
``enable_builtin_telemetry`` option on the Connector or AsyncConnector.

Architecture
------------
A ``_TelemetryProvider`` is created per Connector per project (lazily on
first connect, once the project ID is known). It owns the OTel
``MeterProvider``, ``PeriodicExportingMetricReader``, exporter, and shared
instruments. This means **one background export thread** per project
regardless of how many instances the Connector dials there. Providers are
keyed by project because the exporter writes every time series under the
project it was built with, so instances in a second project need a second
provider to be attributed correctly.

Each instance gets a lightweight ``_MetricRecorder`` that holds pre-built
attribute dicts and a reference to the shared instruments. Instance identity
(project, location, cluster, instance, client_uid) is carried as metric
attributes on every data point. At export time, the custom exporter moves
these from metric labels to the monitored resource labels on each time
series so that Cloud Monitoring associates them with the correct
``alloydb.googleapis.com/InstanceClient`` resource.

Because ``alloydb.googleapis.com`` is a Google-owned metric prefix, the
exporter writes with ``CreateServiceTimeSeries`` rather than the
``CreateTimeSeries`` used by the upstream Cloud Monitoring exporter.
``CreateTimeSeries`` only accepts custom, workload, and external prefixes and
would reject every write.

The two byte counters are asynchronous (observable) instruments: a socket
read or write only bumps an integer on its ``_MetricRecorder``, and the
running totals are read once per export. This keeps an OTel call off the
per-read hot path without the reporting lag of an accumulate-and-flush
scheme, and needs neither a timer nor a thread per connection.

The exporter writes only the six metric types this module defines. The OTel
SDK registers self-observability instruments on whichever ``MeterProvider``
owns the reader, and those would otherwise inherit this exporter's
Google-owned prefix and fail the whole batch.

Limitations
-----------
``bytes_sent_count`` and ``bytes_received_count`` are only reported for the
synchronous ``Connector``, where the driver is handed an
``InstrumentedSocket``. asyncpg exposes no hook for observing bytes on the
wire, so connections made through ``AsyncConnector`` report every other
metric but contribute no byte counts.

``mdx_error`` is only reported by the synchronous ``Connector``. The asyncpg
path performs no metadata exchange, so the status cannot arise there.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import logging
import threading
from typing import Any
from typing import Optional
from typing import Union
import uuid

logger = logging.getLogger(__name__)

_PYTHON_CONNECTOR = "python"

# Meter name — matches Go connector.
_METER_NAME = "alloydb.googleapis.com/client/connector"
# Prefix every exported metric type carries. Same string as the meter name,
# but a distinct concept: the exporter joins it to each instrument name.
_METRIC_PREFIX = "alloydb.googleapis.com/client/connector"
# Monitored resource type — matches Go connector.
_MONITORED_RESOURCE = "alloydb.googleapis.com/InstanceClient"

# Metric names.
_DIAL_COUNT = "dial_count"
_DIAL_LATENCY = "dial_latencies"
_OPEN_CONNECTIONS = "open_connections"
_BYTES_SENT = "bytes_sent_count"
_BYTES_RECEIVED = "bytes_received_count"
_REFRESH_COUNT = "refresh_count"

# The complete set of metric types this connector is allowed to write. The
# exporter drops anything else; see _SystemMetricsExporter._batch_write for
# why that matters.
_EXPORTED_METRIC_TYPES = frozenset(
    f"{_METRIC_PREFIX}/{name}"
    for name in (
        _DIAL_COUNT,
        _DIAL_LATENCY,
        _OPEN_CONNECTIONS,
        _BYTES_SENT,
        _BYTES_RECEIVED,
        _REFRESH_COUNT,
    )
)

# Units, in the UCUM notation Cloud Monitoring expects.
_MILLISECONDS = "ms"
_BYTES = "By"

# Resource attribute keys — used as metric attributes on each data point
# and extracted by the exporter to set per-series monitored resource labels.
_RESOURCE_TYPE_KEY = "gcp.resource_type"
_PROJECT_ID = "project_id"
_LOCATION = "location"
_CLUSTER_ID = "cluster_id"
_INSTANCE_ID = "instance_id"
_CLIENT_UID = "client_uid"

_RESOURCE_LABEL_KEYS = frozenset(
    {_PROJECT_ID, _LOCATION, _CLUSTER_ID, _INSTANCE_ID, _CLIENT_UID}
)

# Metric attribute keys.
_CONNECTOR_TYPE = "connector_type"
_AUTH_TYPE = "auth_type"
_IS_CACHE_HIT = "is_cache_hit"
_STATUS = "status"
_REFRESH_TYPE = "refresh_type"

# Dial status values.
DIAL_SUCCESS = "success"
DIAL_USER_ERROR = "user_error"
DIAL_CACHE_ERROR = "cache_error"
DIAL_TCP_ERROR = "tcp_error"
DIAL_TLS_ERROR = "tls_error"
DIAL_MDX_ERROR = "mdx_error"

# Refresh status values.
REFRESH_SUCCESS = "success"
REFRESH_FAILURE = "failure"

# Refresh type values.
REFRESH_AHEAD_TYPE = "refresh_ahead"
REFRESH_LAZY_TYPE = "lazy"

# Default export interval in milliseconds.
_DEFAULT_EXPORT_INTERVAL_MS = 60_000

# Budget for a single periodic export. The OTel default is 30s, which would
# leave the reader's export thread stuck on one slow write for half an
# export interval.
_EXPORT_TIMEOUT_MS = 10_000

# Budget for the final export on shutdown. The OTel default is 30s, which is
# far longer than the window Connector.close() allows for the whole shutdown
# sequence. The exporter passes this budget down to the write RPC as an
# explicit deadline, so a stalled write fails fast instead of surfacing as a
# TimeoutError out of close().
SHUTDOWN_TIMEOUT_MS = 2_000


@dataclass
class TelemetryAttributes:
    """Holds metadata to attach to a metric recording."""

    iam_authn: bool = False
    cache_hit: bool = False
    dial_status: str = ""
    refresh_status: str = ""
    refresh_type: str = ""


def _auth_type_value(iam_authn: bool) -> str:
    return "iam" if iam_authn else "built_in"


def _version() -> str:
    """The connector version, imported lazily to avoid an import cycle."""
    from google.cloud.alloydbconnector.version import __version__

    return __version__


class NullTelemetryProvider:
    """A no-op TelemetryProvider for when telemetry is disabled."""

    def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
        pass

    def create_metric_recorder(
        self,
        project_id: str,
        location: str,
        cluster: str,
        instance: str,
    ) -> MetricRecorderType:
        return NullMetricRecorder()


class NullMetricRecorder:
    """A no-op MetricRecorder for when telemetry is disabled."""

    def record_dial_count(self, attrs: TelemetryAttributes) -> None:
        pass

    def record_dial_latency(self, latency_ms: float) -> None:
        pass

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        pass

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        pass

    def record_bytes_rx(self, count: int) -> None:
        pass

    def record_bytes_tx(self, count: int) -> None:
        pass

    def record_refresh_count(self, attrs: TelemetryAttributes) -> None:
        pass


@dataclass(frozen=True)
class _Instruments:
    """The synchronous OTel instruments shared by every _MetricRecorder.

    The two byte counters are deliberately absent: they are asynchronous
    (observable) instruments driven by a callback at collection time rather
    than written to on the socket hot path. See
    ``_TelemetryProvider._observe_bytes``.
    """

    dial_count: Any
    dial_latency: Any
    open_connections: Any
    refresh_count: Any


@lru_cache(maxsize=1)
def _system_metrics_exporter_class() -> Any:
    """Build the exporter subclass that writes system metrics.

    The Cloud Monitoring exporter is imported here rather than at module
    scope so that a missing or broken install degrades to
    NullTelemetryProvider instead of breaking ``import``. The class itself is
    built once per process rather than once per Connector.
    """
    from opentelemetry.exporter.cloud_monitoring import MAX_BATCH_WRITE
    from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
    from opentelemetry.sdk.metrics.export import MetricExportResult
    from opentelemetry.sdk.metrics.export import Sum

    from google.api import metric_pb2
    from google.cloud.monitoring_v3 import CreateTimeSeriesRequest

    # This exporter overrides private hooks of the upstream exporter. If a
    # future release renames them, the overrides would silently stop being
    # called and metrics would be written in the wrong shape. Fail fast
    # here instead so the caller falls back to NullTelemetryProvider.
    for _hook in ("_batch_write", "_get_metric_descriptor"):
        if not hasattr(CloudMonitoringMetricsExporter, _hook):
            raise AttributeError(
                f"cloud monitoring exporter is missing {_hook}; "
                "the installed version is not supported"
            )

    class _SystemMetricsExporter(CloudMonitoringMetricsExporter):
        """Exporter that extracts instance labels from metric attributes
        and sets them as monitored resource labels on each time series."""

        # Set by _batch_write, consumed by export. The upstream export()
        # catches write failures itself, so they cannot be observed by
        # wrapping super().export() in a try block.
        _write_error: Optional[BaseException] = None
        # Deadline for the write RPC, derived from the budget the metric
        # reader gives each export.
        _write_timeout_s: Optional[float] = None
        # Ceiling on that deadline, tightened by _TelemetryProvider.shutdown.
        # The metric reader hands the *export interval* to the final export
        # rather than the shutdown deadline, so the budget close() allows has
        # to be applied here.
        _max_write_timeout_s: Optional[float] = None

        def export(
            self,
            metrics_data: Any,
            timeout_millis: float = 10_000,
            **kwargs: Any,
        ) -> Any:
            self._write_error = None
            # Upstream leaves timeout_millis unused (it carries its own TODO
            # to pass it down) and create_service_time_series has no default
            # deadline, so the export budget is only honored if the RPC gets
            # it from here. Without it, a stalled write blocks shutdown well
            # past the budget Connector.close() allows.
            budget_s = max(timeout_millis, 0) / 1000
            if self._max_write_timeout_s is not None:
                budget_s = min(budget_s, self._max_write_timeout_s)
            self._write_timeout_s = budget_s
            try:
                result = super().export(metrics_data, timeout_millis, **kwargs)
            except Exception as e:
                # Failures upstream does not catch itself, e.g. while
                # converting data points into time series.
                logger.debug(f"Built-in metrics export failed: {e}")
                return MetricExportResult.FAILURE
            if self._write_error is not None:
                # The write failed. Upstream would have reported this with
                # logger.error on a logger this library does not own, so
                # _batch_write hands the error over instead of raising.
                logger.debug(f"Built-in metrics export failed: {self._write_error}")
                self._write_error = None
                return MetricExportResult.FAILURE
            return result

        def _batch_write(self, series: Any) -> None:
            # Drop anything this connector did not define. The OTel SDK
            # registers its own self-observability instruments (e.g.
            # otel.sdk.metric_reader.collection.duration) on whichever
            # MeterProvider owns the reader, so from the second export
            # onward they arrive here wearing this exporter's
            # alloydb.googleapis.com prefix and carrying none of the
            # instance labels. Writing an undefined type under a
            # Google-owned prefix fails the whole batch, which would take
            # every real metric down with it.
            series = [ts for ts in series if ts.metric.type in _EXPORTED_METRIC_TYPES]
            if not series:
                return
            # Because all instances share a single MeterProvider,
            # instance identity (project, location, cluster, instance,
            # client_uid) arrives here as metric labels rather than
            # resource labels. We pop them from metric labels and set
            # them as the monitored resource labels so Cloud Monitoring
            # associates each time series with the correct
            # InstanceClient resource.
            for ts in series:
                ts.resource.type = _MONITORED_RESOURCE
                ts.resource.labels.clear()
                for key in _RESOURCE_LABEL_KEYS:
                    val = ts.metric.labels.pop(key, None)
                    if val is not None:
                        ts.resource.labels[key] = val
            # Deliberately not super()._batch_write(), which calls
            # CreateTimeSeries. That RPC only accepts custom, workload,
            # and external metric prefixes and rejects the Google-owned
            # alloydb.googleapis.com prefix these metrics use. Matches
            # WithCreateServiceTimeSeries() in the Go connector.
            write_ind = 0
            try:
                while write_ind < len(series):
                    self.client.create_service_time_series(
                        CreateTimeSeriesRequest(
                            name=self.project_name,
                            time_series=series[write_ind : write_ind + MAX_BATCH_WRITE],
                        ),
                        timeout=self._write_timeout_s,
                    )
                    write_ind += MAX_BATCH_WRITE
            except Exception as e:
                # Report the failure through export() rather than raising:
                # upstream catches anything raised here and logs it with
                # logger.error on the exporter's own logger, which would
                # turn a missing IAM permission into an ERROR and a stack
                # trace every export interval.
                self._write_error = e

        def _get_metric_descriptor(self, metric: Any) -> Any:
            descriptor_type = f"{self._prefix}/{metric.name}"
            if descriptor_type in self._metric_descriptors:
                return self._metric_descriptors[descriptor_type]

            data = metric.data
            if isinstance(data, Sum):
                metric_kind = (
                    metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE
                    if data.is_monotonic
                    else metric_pb2.MetricDescriptor.MetricKind.GAUGE
                )
            else:
                metric_kind = metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE

            descriptor = metric_pb2.MetricDescriptor(
                type=descriptor_type,
                metric_kind=metric_kind,
                unit=metric.unit or "",
            )
            self._metric_descriptors[descriptor_type] = descriptor
            return descriptor

    return _SystemMetricsExporter


class _TelemetryProvider:
    """Owns a single MeterProvider shared across all instances.

    Created once per Connector per project (lazily on first connect). Holds
    the OTel MeterProvider, PeriodicExportingMetricReader (one background
    thread), and the shared instrument objects (counters, histogram).

    Call ``create_metric_recorder`` to get a lightweight per-instance
    ``_MetricRecorder`` that records to the shared instruments with
    instance-specific attributes.
    """

    def __init__(
        self,
        project_id: str,
        client_uid: str,
        version: str,
        monitoring_client: Any,
    ) -> None:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        # Every recorder this provider has handed out. The observable byte
        # counters walk it at collection time. Bounded by the number of
        # instances the Connector dials in this project.
        self._recorders: list[_MetricRecorder] = []

        # Carried for parity with the Go connector, where the exporter reads
        # gcp.resource_type to pick the monitored resource. The Python
        # exporter ignores it -- it maps from cloud.platform -- and
        # _batch_write overwrites ts.resource wholesale anyway, so this
        # attribute has no effect on what is written.
        resource = Resource.create({_RESOURCE_TYPE_KEY: _MONITORED_RESOURCE})

        exporter = _system_metrics_exporter_class()(
            project_id=project_id,
            client=monitoring_client,
            prefix=_METRIC_PREFIX,
        )

        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=_DEFAULT_EXPORT_INTERVAL_MS,
            export_timeout_millis=_EXPORT_TIMEOUT_MS,
        )

        self._exporter = exporter

        self._provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )

        meter = self._provider.get_meter(
            _METER_NAME,
            version=version,
        )

        self._instruments = _Instruments(
            dial_count=meter.create_counter(_DIAL_COUNT),
            dial_latency=meter.create_histogram(_DIAL_LATENCY, unit=_MILLISECONDS),
            open_connections=meter.create_up_down_counter(_OPEN_CONNECTIONS),
            refresh_count=meter.create_counter(_REFRESH_COUNT),
        )

        # Byte counts are observable counters rather than synchronous ones.
        # A socket read or write only bumps an integer on its recorder; the
        # totals are read here once per export. That keeps an OTel call
        # (~1us) off the per-read/per-write hot path without the staleness
        # of the accumulate-and-flush scheme it replaces: whatever has been
        # transferred by collection time is what gets reported, with no
        # timer and no per-connection thread.
        #
        # Keep references: an observable instrument that is garbage
        # collected stops being collected.
        self._bytes_rx_instrument = meter.create_observable_counter(
            _BYTES_RECEIVED,
            callbacks=[self._observe_bytes_rx],
            unit=_BYTES,
        )
        self._bytes_tx_instrument = meter.create_observable_counter(
            _BYTES_SENT,
            callbacks=[self._observe_bytes_tx],
            unit=_BYTES,
        )

        self._client_uid = client_uid

    def _observe_bytes(self, received: bool) -> list[Any]:
        """Report each recorder's running byte total as one Observation.

        Recorders with a zero total are skipped so that a connector which
        never transfers bytes -- every AsyncConnector, which has no socket
        hook -- does not create an all-zero time series per instance.
        """
        from opentelemetry.metrics import Observation

        observations = []
        for recorder in self._recorders:
            total = recorder.bytes_rx_total() if received else recorder.bytes_tx_total()
            if total:
                observations.append(Observation(total, recorder.static_attrs))
        return observations

    def _observe_bytes_rx(self, options: Any) -> list[Any]:
        return self._observe_bytes(received=True)

    def _observe_bytes_tx(self, options: Any) -> list[Any]:
        return self._observe_bytes(received=False)

    def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> None:
        # Bound the final export. The OTel default of 30s is far longer than
        # the window Connector.close() allows for the whole shutdown, and the
        # metric reader hands the export interval, not the shutdown deadline,
        # to that last export. Cap the write RPC so the flush actually fits
        # in the budget.
        self._exporter._max_write_timeout_s = max(timeout_millis, 0) / 1000
        self._provider.shutdown(timeout_millis=timeout_millis)

    def create_metric_recorder(
        self,
        project_id: str,
        location: str,
        cluster: str,
        instance: str,
    ) -> _MetricRecorder:
        """Create a lightweight MetricRecorder for a specific instance."""
        recorder = _MetricRecorder(
            instruments=self._instruments,
            project_id=project_id,
            location=location,
            cluster=cluster,
            instance=instance,
            client_uid=self._client_uid,
        )
        self._recorders.append(recorder)
        return recorder


class _MetricRecorder:
    """Lightweight per-instance recorder that delegates to shared instruments.

    Created by ``_TelemetryProvider.create_metric_recorder``. Holds pre-built
    attribute dicts that include both metric attributes (connector_type, etc.)
    and resource-identifying labels (project, location, cluster, instance,
    client_uid). The exporter's ``_batch_write`` moves the resource labels
    from metric labels to the monitored resource on each time series.

    Hot-path methods (``record_bytes_rx``, ``record_bytes_tx``) only take a
    lock and add to an integer; the running totals are read once per export
    by the provider's observable byte counters.
    """

    def __init__(
        self,
        instruments: _Instruments,
        project_id: str,
        location: str,
        cluster: str,
        instance: str,
        client_uid: str,
    ) -> None:
        self._i = instruments

        # Running byte totals, published by the provider's observable
        # counters. int += is not atomic under free threading, so the lock
        # is load bearing; it is only ever held for the arithmetic, never
        # across I/O.
        self._bytes_lock = threading.Lock()
        self._bytes_rx = 0
        self._bytes_tx = 0

        # Resource-identifying labels included in every metric data point.
        resource_labels = {
            _PROJECT_ID: project_id,
            _LOCATION: location,
            _CLUSTER_ID: cluster,
            _INSTANCE_ID: instance,
            _CLIENT_UID: client_uid,
        }

        # Pre-built attributes for the metrics that carry no per-call keys
        # (bytes rx/tx and dial latency), so that recording a socket read or
        # write allocates nothing.
        self._static_attrs = {
            _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
            **resource_labels,
        }

        # Base attrs for methods that add dynamic keys per call.
        self._resource_labels = resource_labels

    def record_dial_count(self, attrs: TelemetryAttributes) -> None:
        self._i.dial_count.add(
            1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                _IS_CACHE_HIT: str(attrs.cache_hit).lower(),
                _STATUS: attrs.dial_status,
                **self._resource_labels,
            },
        )

    def record_dial_latency(self, latency_ms: float) -> None:
        self._i.dial_latency.record(latency_ms, self._static_attrs)

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        self._i.open_connections.add(
            1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                **self._resource_labels,
            },
        )

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self._i.open_connections.add(
            -1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                **self._resource_labels,
            },
        )

    def record_bytes_rx(self, count: int) -> None:
        with self._bytes_lock:
            self._bytes_rx += count

    def record_bytes_tx(self, count: int) -> None:
        with self._bytes_lock:
            self._bytes_tx += count

    def bytes_rx_total(self) -> int:
        """The cumulative bytes received, for the observable counter."""
        with self._bytes_lock:
            return self._bytes_rx

    def bytes_tx_total(self) -> int:
        """The cumulative bytes sent, for the observable counter."""
        with self._bytes_lock:
            return self._bytes_tx

    @property
    def static_attrs(self) -> dict[str, str]:
        """Attributes for metrics that carry no per-call keys."""
        return self._static_attrs

    def record_refresh_count(self, attrs: TelemetryAttributes) -> None:
        self._i.refresh_count.add(
            1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _STATUS: attrs.refresh_status,
                _REFRESH_TYPE: attrs.refresh_type,
                **self._resource_labels,
            },
        )


# Type alias for use in type annotations.
MetricRecorderType = Union[_MetricRecorder, NullMetricRecorder]
TelemetryProviderType = Union[_TelemetryProvider, NullTelemetryProvider]


# Hard ceiling on the telemetry shutdown. The provider is given
# SHUTDOWN_TIMEOUT_MS to flush, plus a little slack to return on its own.
# Neither the Cloud Monitoring exporter nor the write RPC guarantees it
# returns within that budget, so the connector bounds the wait itself.
TELEMETRY_SHUTDOWN_TIMEOUT_S = SHUTDOWN_TIMEOUT_MS / 1000 + 0.5


class _TelemetryMixin:
    """Built-in telemetry wiring shared by Connector and AsyncConnector.

    Both connectors need the same provider-per-project bookkeeping, the same
    lazily created per-instance recorders, and the same bounded shutdown.
    Keeping one copy means the two cannot drift on shutdown semantics, which
    is the part most likely to go quietly wrong.

    Subclasses must call ``_init_telemetry`` from ``__init__``.
    """

    def _init_telemetry(self, enabled: bool, credentials: Any) -> None:
        self._enable_builtin_telemetry = enabled
        self._client_uid = str(uuid.uuid4())
        self._metric_recorders: dict[str, MetricRecorderType] = {}
        self._telemetry_providers: dict[str, TelemetryProviderType] = {}
        self._monitoring_client: Any = None
        if not enabled:
            return
        try:
            from google.cloud.monitoring_v3 import MetricServiceClient

            self._monitoring_client = MetricServiceClient(credentials=credentials)
        except Exception as e:
            logger.debug(f"Built-in metrics exporter failed to initialize: {e}")

    def _get_telemetry_provider(self, project_id: str) -> TelemetryProviderType:
        """Get or lazily create the TelemetryProvider for a project.

        Providers are keyed by project because the exporter writes every time
        series it produces under the project it was built with. A Connector
        that dials instances in more than one project needs one provider per
        project, or the second project's metrics are written to the first.
        """
        provider = self._telemetry_providers.get(project_id)
        if provider is not None:
            return provider
        provider = new_telemetry_provider(
            enabled=self._enable_builtin_telemetry,
            project_id=project_id,
            client_uid=self._client_uid,
            version=_version(),
            monitoring_client=self._monitoring_client,
        )
        self._telemetry_providers[project_id] = provider
        return provider

    def _metric_recorder(self, instance_uri: str) -> MetricRecorderType:
        """Get or lazily create a MetricRecorder for the given instance."""
        if instance_uri in self._metric_recorders:
            return self._metric_recorders[instance_uri]
        from google.cloud.alloydbconnector.instance import _parse_instance_uri

        project, region, cluster, name = _parse_instance_uri(instance_uri)
        provider = self._get_telemetry_provider(project)
        mr = provider.create_metric_recorder(
            project_id=project,
            location=region,
            cluster=cluster,
            instance=name,
        )
        self._metric_recorders[instance_uri] = mr
        return mr

    @staticmethod
    def _shutdown_providers(providers: list[TelemetryProviderType]) -> None:
        """Flush and shut down telemetry providers. Runs in an executor."""
        for provider in providers:
            provider.shutdown()

    async def _shutdown_telemetry(self) -> None:
        """Shut down the built-in telemetry providers and monitoring client.

        The final metric export runs in an executor so its gRPC call does not
        block the event loop, and the wait is bounded: a stalled export must
        not hold up closing the connector. Failures here are never actionable
        by the caller, so they are logged at debug and swallowed.

        Bounding the wait does not bound the work. The default executor's
        threads are joined at interpreter exit, so a wedged export would
        block process teardown even though close() returned. What actually
        caps it is the deadline _TelemetryProvider.shutdown puts on the write
        RPC; do not remove that.
        """
        providers = list(self._telemetry_providers.values())
        self._telemetry_providers.clear()
        self._metric_recorders.clear()
        if providers:
            loop = asyncio.get_event_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._shutdown_providers, providers),
                    timeout=TELEMETRY_SHUTDOWN_TIMEOUT_S,
                )
            except Exception as e:
                logger.debug(f"Built-in metrics failed to shut down cleanly: {e}")
        if self._monitoring_client is not None:
            # Release the gRPC channel opened in __init__.
            try:
                self._monitoring_client.close()
            except Exception as e:
                logger.debug(f"Monitoring client failed to close cleanly: {e}")
            self._monitoring_client = None


def new_telemetry_provider(
    enabled: bool,
    project_id: str,
    client_uid: str,
    version: str,
    monitoring_client: Any = None,
) -> TelemetryProviderType:
    """Create a new TelemetryProvider.

    Returns a NullTelemetryProvider if telemetry is disabled or if
    initialization fails.
    """
    if not enabled:
        logger.debug("Disabling built-in metrics")
        return NullTelemetryProvider()
    if monitoring_client is None:
        logger.debug("Metric client is None, disabling built-in metrics")
        return NullTelemetryProvider()
    try:
        return _TelemetryProvider(
            project_id=project_id,
            client_uid=client_uid,
            version=version,
            monitoring_client=monitoring_client,
        )
    except Exception as e:
        logger.debug(f"Built-in metrics exporter failed to initialize: {e}")
        return NullTelemetryProvider()
