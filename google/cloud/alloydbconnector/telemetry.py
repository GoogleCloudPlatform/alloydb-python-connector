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

This module provides an internal metrics collection using OpenTelemetry with a
Cloud Monitoring exporter. Metrics are exported to the
alloydb.googleapis.com/client/connector system metric prefix. Presently,
these metrics aren't public but will be made public in the future.

The telemetry is enabled by default and can be disabled via the
"enable_builtin_telemetry" option on the Connector or AsyncConnector.

Architecture
------------
A _TelemetryProvider is created per Connector per project (lazily on
the first connection, once the project ID is known). It owns the OTel
MeterProvider, PeriodicExportingMetricReader, exporter, and shared
instruments. This means **one background export thread** per project
regardless of how many instances the Connector dials there. Project
keys providers because the exporter writes every time series under the
project it was built with, so instances in a second project need a second
provider to be attributed correctly.

Each instance gets a lightweight _MetricRecorder that holds pre-built
attribute dicts and a reference to the shared instruments. InstanceClient identity
(project, location, cluster, instance, client_uid) is carried as metric
attributes on every data point. At export time, the custom exporter moves
these from metric labels to the monitored resource labels on each time
series so that Cloud Monitoring associates them with the correct
alloydb.googleapis.com/InstanceClient resource.

Because alloydb.googleapis.com is a Google-owned metric prefix, the
exporter writes with CreateServiceTimeSeries rather than the
CreateTimeSeries used by the upstream Cloud Monitoring exporter.
CreateTimeSeries only accepts custom, workload, and external prefixes and
would reject every write.

The two byte counters are asynchronous (observable) instruments: a socket
read or write only bumps an integer on its _MetricRecorder, and the
running totals are read once per export. This keeps an OTel call off the
per-read hot path without the reporting lag of an accumulate-and-flush
scheme, and needs neither a timer nor a thread per connection.

The exporter writes only the six metric types this module defines. The OTel
SDK registers self-observability instruments on whichever MeterProvider
owns the reader, and those would otherwise inherit this exporter's
Google-owned prefix and fail the whole batch.

Limitations
-----------
bytes_sent_count and bytes_received_count are only reported for the
synchronous Connector, where the driver is handed an
InstrumentedSocket. asyncpg exposes no hook for observing bytes on the
wire, so connections made through AsyncConnector report every other
metric but contribute no byte counts.

mdx_error is only reported by the synchronous Connector. The asyncpg
path performs no metadata exchange, so the status cannot arise there.
"""

from __future__ import annotations

import asyncio
import atexit
from dataclasses import dataclass
from functools import lru_cache
import logging
import threading
from time import monotonic
from typing import Any
from typing import Optional
from typing import Union
import uuid
import warnings

logger = logging.getLogger(__name__)

_PYTHON_CONNECTOR = "python"

# Meter name — matches Go connector.
_METER_NAME = "alloydb.googleapis.com/client/connector"
# Prefix every exported metric type carries. The same string as the meter name,
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
#
# Connector.close() caps the *entire* close sequence at 3s, and the connection
# info caches have to fit in it too, so telemetry takes a third of that rather
# than most of it. TELEMETRY_SHUTDOWN_TIMEOUT_S adds 0.5s of slack on top.
SHUTDOWN_TIMEOUT_MS = 1_000


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


class _UnsupportedExporter(Exception):
    """A dependency no longer has something the exporter overrides need.

    Either a release renamed a private hook or attribute, or the monitoring
    client predates the write method _batch_write calls. Separated from any
    other startup failure because it is the one worth telling the operator
    about: metrics stay off until the dependency changes, rather than being a
    transient problem that may fix itself.

    The message carries its own remediation, because which package to change
    depends on which check failed.
    """


class NullTelemetryProvider:
    """A no-op TelemetryProvider for when telemetry is disabled."""

    def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> bool:
        return True

    @staticmethod
    def create_metric_recorder(
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
    _TelemetryProvider._observe_bytes.
    """

    dial_count: Any
    dial_latency: Any
    open_connections: Any
    refresh_count: Any


# Upstream instance state the overrides read: _batch_write writes through
# the client to project_name, and _get_metric_descriptor keys off
# _prefix and caches into _metric_descriptors. A rename of
# these slips past the class-level hook check in
# _system_metrics_exporter_class, because they are only set in the upstream's
# __init__, so the constructed exporter is checked against this list.
_EXPORTER_ATTRS = ("client", "project_name", "_prefix", "_metric_descriptors")

# The write method _batch_write calls in place of super()._batch_write. It
# lives on the monitoring client rather than the exporter, so the
# _EXPORTER_ATTRS loop cannot see it, but it is just as load bearing: a client
# too old to have it would surface only as a debug log out of every export,
# once a minute, for the life of the process.
_CLIENT_WRITE_METHOD = "create_service_time_series"


# The cache memoizes the class, so every provider in the process subclasses
# the same object and the imports and hook checks below run once. lru_cache
# does not memoize exceptions, though: on an unsupported install the guard
# below re-raises on every call rather than being remembered. That is bounded
# by _get_telemetry_provider, which caches the resulting
# NullTelemetryProvider per project, so an unsupported install costs one
# re-import per project rather than one per dial.
@lru_cache(maxsize=1)
def _system_metrics_exporter_class() -> Any:
    """Build the exporter subclass that writes system metrics.

    The Cloud Monitoring exporter is imported here rather than at module
    scope so that a missing or broken install degrades to
    NullTelemetryProvider instead of breaking the import. The class itself is
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
    # here instead, so the caller falls back to NullTelemetryProvider.
    #
    # The overrides depend on upstream's instance state as well. Those are
    # not on the class, so _TelemetryProvider checks them against the
    # constructed exporter; see _EXPORTER_ATTRS.
    for _hook in ("_batch_write", "_get_metric_descriptor"):
        if not hasattr(CloudMonitoringMetricsExporter, _hook):
            raise _UnsupportedExporter(
                f"cloud monitoring exporter is missing {_hook}. Install a "
                "supported version of opentelemetry-exporter-gcp-monitoring "
                "to re-enable them"
            )

    class _SystemMetricsExporter(CloudMonitoringMetricsExporter):
        """Exporter that extracts instance labels from metric attributes
        and sets them as monitored resource labels on each time series."""

        # Set by _batch_write, consumed by export. The upstream export()
        # catches write failures itself, so they cannot be observed by
        # wrapping super().export() in a try block.
        _write_error: Optional[BaseException] = None
        # Deadline for to write RPC, derived from the budget the metric
        # reader gives each export.
        _write_timeout_s: Optional[float] = None
        # Ceiling on that deadline, tightened by _TelemetryProvider.shutdown.
        # The metric reader hands the *export interval* to the final export
        # rather than the shutdown deadline, so the budget close() allows has
        # to be applied here.
        _max_write_timeout_s: Optional[float] = None

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # Whether a write RPC is outstanding. Only the metric reader's
            # export thread ever changes it -- the reader holds its own lock
            # across export() -- but _TelemetryProvider.shutdown reads it from
            # another thread, so the lock is what makes that read well
            # defined.
            self._write_lock = threading.Lock()
            self._writes_in_flight = 0

        @property
        def write_in_flight(self) -> bool:
            """Whether a write RPC has been issued and has not returned."""
            with self._write_lock:
                return self._writes_in_flight > 0

        def export(
            self,
            metrics_data: Any,
            timeout_millis: float = _EXPORT_TIMEOUT_MS,
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
            # Drop anything this connector did not define. A consumer who
            # sets OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED gets the SDK's
            # own self-observability instruments (e.g.
            # otel.sdk.metric_reader.collection.duration) registered on
            # whichever MeterProvider owns the reader, so they arrive here
            # wearing this exporter's alloydb.googleapis.com prefix and
            # carrying none of the instance labels. Writing an undefined type
            # under a Google-owned prefix fails the whole batch, which would
            # take every real metric down with it. That env var is global, so
            # an application that opts itself in opts this connector in too.
            #
            # _get_metric_descriptor already drops these upstream of here.
            # This is the backstop for a series that reaches the write by
            # some other path.
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
            #
            # One deadline for the whole export rather than one per chunk.
            # Past MAX_BATCH_WRITE series -- six metrics times ~34 instances
            # in a single project -- this loop issues more than one RPC, and
            # giving each the full budget would let a write that is supposed
            # to be bounded by SHUTDOWN_TIMEOUT_MS take a multiple of it.
            budget_s = self._write_timeout_s
            deadline = None if budget_s is None else monotonic() + budget_s
            write_ind = 0
            # Published for the whole loop, not per RPC: what shutdown needs to
            # know is whether this thread is still somewhere inside the write,
            # since that is what makes closing the channel unsafe.
            with self._write_lock:
                self._writes_in_flight += 1
            try:
                while write_ind < len(series):
                    timeout_s = None
                    if deadline is not None:
                        timeout_s = deadline - monotonic()
                        if timeout_s <= 0:
                            raise TimeoutError(
                                "ran out of time writing built-in metrics after "
                                f"{write_ind} of {len(series)} time series"
                            )
                    self.client.create_service_time_series(
                        CreateTimeSeriesRequest(
                            name=self.project_name,
                            time_series=series[write_ind : write_ind + MAX_BATCH_WRITE],
                        ),
                        timeout=timeout_s,
                    )
                    write_ind += MAX_BATCH_WRITE
            except Exception as e:
                # Report the failure through export() rather than raising:
                # upstream catches anything raised here and logs it with
                # logger.error on the exporter's own logger, which would
                # turn a missing IAM permission into an ERROR and a stack
                # trace every export interval.
                self._write_error = e
            finally:
                with self._write_lock:
                    self._writes_in_flight -= 1

        def _get_metric_descriptor(self, metric: Any) -> Any:
            descriptor_type = f"{self._prefix}/{metric.name}"
            # Drop anything this connector did not define, here rather than
            # in _batch_write: returning None makes upstream skip the metric
            # before it converts a single data point. Upstream derives the
            # point's time interval from the metric kind this method
            # returns, and a foreign instrument whose kind is guessed wrong
            # -- a Gauge, whose data points carry no start time -- raises
            # during that conversion and fails the *whole* export, taking
            # every real metric with it. Guessing is unavoidable: the kinds
            # below cover only what this module records.
            if descriptor_type not in _EXPORTED_METRIC_TYPES:
                return None
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

    Call create_metric_recorder to get a lightweight per-instance
    _MetricRecorder that records to the shared instruments with
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

        # Set once the provider is fully built; see the MeterProvider
        # construction below for why this module registers its own.
        self._atexit_handler: Optional[Any] = None

        # Carried for parity with the Go connector, where the exporter reads
        # gcp.resource_type to pick the monitored resource. The Python
        # exporter ignores it -- it maps from cloud.platform -- and
        # _batch_write overwrites ts.resource wholesale anyway, so this
        # attribute has no effect on what is written.
        #
        # Deliberately the constructor and not Resource.create(), which is not
        # inert: it reads the consumer's OTEL_SERVICE_NAME and
        # OTEL_RESOURCE_ATTRIBUTES, mints a service.instance.id, and under
        # OTEL_EXPERIMENTAL_RESOURCE_DETECTORS scans entry points and runs
        # third-party detectors -- opentelemetry-resourcedetector-gcp arrives
        # as a transitive dependency of the exporter and registers two, which
        # reach for the metadata server and log a traceback on the OTel logger
        # when they fail. All of that would land on the first dial, in service
        # of a resource that changes nothing about the write.
        resource = Resource(attributes={_RESOURCE_TYPE_KEY: _MONITORED_RESOURCE})

        # Upstream marks CloudMonitoringMetricsExporter @deprecated as of
        # 1.13, which warns both when it is subclassed (the first call below
        # builds the class) and when it is instantiated. Its replacement is
        # the OTLP exporter, which cannot write a Google-owned metric prefix
        # at all, so there is nothing to migrate to for system metrics and
        # the warning is not actionable by anyone downstream. Swallow it at
        # this one site rather than let it land in every consumer's test
        # output -- and, under -W error, abort the exporter and silently
        # disable metrics. A release that removes the class outright raises
        # ImportError from _system_metrics_exporter_class and falls back like
        # any other startup failure.
        #
        # catch_warnings is briefly process-global; the window is one class
        # build and one construction, both done once per process.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            exporter = _system_metrics_exporter_class()(
                project_id=project_id,
                client=monitoring_client,
                prefix=_METRIC_PREFIX,
            )
        for attr in _EXPORTER_ATTRS:
            if not hasattr(exporter, attr):
                raise _UnsupportedExporter(
                    f"cloud monitoring exporter is missing {attr}. Install a "
                    "supported version of opentelemetry-exporter-gcp-monitoring "
                    "to re-enable them"
                )
        if not hasattr(exporter.client, _CLIENT_WRITE_METHOD):
            raise _UnsupportedExporter(
                f"monitoring client is missing {_CLIENT_WRITE_METHOD}. "
                "Upgrade google-cloud-monitoring to re-enable them"
            )

        # Cap every write, not just the one on shutdown. The metric reader
        # hands the *export interval* to its final export, so without a
        # ceiling set here any export could sit in a 60s-deadline write.
        # _TelemetryProvider.shutdown only tightens this.
        exporter._max_write_timeout_s = _EXPORT_TIMEOUT_MS / 1000

        # Constructing the reader starts its export thread, so everything
        # after this point runs under a try: new_telemetry_provider turns a
        # failure here into a NullTelemetryProvider, and without the cleanup
        # the half-built provider's thread would go on exporting every
        # interval for the life of the process with no way to reach it.
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=_DEFAULT_EXPORT_INTERVAL_MS,
            export_timeout_millis=_EXPORT_TIMEOUT_MS,
        )
        provider = None
        try:
            self._exporter = exporter

            # shutdown_on_exit=False and an atexit handler of this module's
            # own, because MeterProvider's calls shutdown() with OTel's 30s
            # default and the reader hands the export interval to that last
            # export: a connector that is never closed -- the common case for
            # a script, with metrics on by default -- would hang for the full
            # _EXPORT_TIMEOUT_MS at interpreter exit on a stalled write, ten
            # times the budget close() allows for the same flush. Registering
            # it here means the at-exit path gets SHUTDOWN_TIMEOUT_MS, the
            # same as every other path. shutdown() unregisters it, so a
            # connector that *is* closed does not flush twice.
            provider = MeterProvider(
                resource=resource,
                metric_readers=[reader],
                shutdown_on_exit=False,
            )
            self._provider = provider

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

            # Byte counts are observable counters rather than synchronous
            # ones. A socket read or write only bumps an integer on its
            # recorder; the totals are read here once per export. That keeps
            # an OTel call (~1us) off the per-read/per-write hot path without
            # the staleness of the accumulate-and-flush scheme it replaces:
            # whatever has been transferred by collection time is what gets
            # reported, with no timer and no per-connection thread.
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

            # Last, so a provider that failed to build leaves nothing
            # registered against it.
            self._atexit_handler = atexit.register(self.shutdown)
        except BaseException:
            # Stop the export thread this __init__ started, shutting down the
            # MeterProvider once it exists rather than just the reader.
            try:
                if provider is not None:
                    provider.shutdown()
                else:
                    reader.shutdown()
            except Exception as e:  # pragma: no cover - cleanup is best effort
                logger.debug(f"Built-in metrics failed to clean up: {e}")
            raise

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

    def shutdown(self, timeout_millis: float = SHUTDOWN_TIMEOUT_MS) -> bool:
        """Flush and stop, reporting whether the final write finished.

        A False return means a write may still be in flight on the reader's
        export thread, which is what decides whether the monitoring channel
        can be closed. Returning cleanly does not imply it: the metric reader
        joins its export thread with a *timeout* and does not raise when that
        join expires, and MeterProvider.shutdown only reports exceptions, so a
        shutdown that gives up on a stalled write is indistinguishable from
        one that flushed. The exporter knows, so ask it.
        """
        if self._atexit_handler is not None:
            atexit.unregister(self._atexit_handler)
            self._atexit_handler = None
        # Tighten the cap set in __init__ to the budget close() allows. The
        # metric reader hands the export interval, not the shutdown deadline,
        # to that last export, so the RPC has to be capped from here for the
        # flush to actually fit in the budget.
        self._exporter._max_write_timeout_s = max(timeout_millis, 0) / 1000
        self._provider.shutdown(timeout_millis=timeout_millis)
        return not self._exporter.write_in_flight

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

    Created by _TelemetryProvider.create_metric_recorder. Holds pre-built
    attribute dicts that include both metric attributes (connector_type, etc.)
    and resource-identifying labels (project, location, cluster, instance,
    client_uid). The exporter's _batch_write moves the resource labels
    from metric labels to the monitored resource on each time series.

    Hot-path methods (record_bytes_rx, record_bytes_tx) only take a
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
        self._instruments = instruments

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
        self._instruments.dial_count.add(
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
        self._instruments.dial_latency.record(latency_ms, self._static_attrs)

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        self._instruments.open_connections.add(
            1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                **self._resource_labels,
            },
        )

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self._instruments.open_connections.add(
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
        self._instruments.refresh_count.add(
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
        self._telemetry_credentials = credentials
        self._client_uid = ""
        self._telemetry_started = False
        self._metric_recorders: dict[str, MetricRecorderType] = {}
        self._telemetry_providers: dict[str, TelemetryProviderType] = {}
        self._monitoring_client: Any = None

    def _start_telemetry(self) -> None:
        """Mint the client identity and open the monitoring channel.

        Deferred to the first dial rather than done in __init__ so that both
        land after any fork. AsyncConnector.__init__ needs no running event
        loop, so building one at module scope and forking workers -- gunicorn
        or uvicorn with --preload -- is a supported pattern, and a client_uid
        inherited across that fork would put every worker on the same
        cumulative time series, each overwriting the others' points. The gRPC
        channel has the same problem in reverse: opened before the fork, used
        after it.

        It also keeps the google.cloud.monitoring_v3 import, which costs more
        than the rest of this package put together, off the constructor for a
        connector that is never dialed.

        Both connectors create recorders from a single thread -- the sync
        Connector's background event loop, or the caller's -- so this needs no
        lock.
        """
        self._telemetry_started = True
        self._client_uid = str(uuid.uuid4())
        if not self._enable_builtin_telemetry:
            return
        try:
            from google.cloud.monitoring_v3 import MetricServiceClient

            self._monitoring_client = MetricServiceClient(
                credentials=self._telemetry_credentials
            )
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
        if not self._telemetry_started:
            self._start_telemetry()
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

    async def _shutdown_telemetry(self) -> None:
        """Shut down the built-in telemetry providers and monitoring client.

        Each provider's final metric export runs in an executor so its gRPC
        call does not block the event loop, and the wait is bounded: a
        stalled export must not hold up closing the connector. Failures here
        are never actionable by the caller, so they are logged at debug and
        swallowed.

        The providers are flushed concurrently rather than one after another.
        Each gets the full SHUTDOWN_TIMEOUT_MS for its own write, so flushing
        them in sequence would need a wait proportional to the number of
        projects dialed; concurrently, TELEMETRY_SHUTDOWN_TIMEOUT_S covers
        any number of them.

        A provider reports whether its own flush finished, because returning
        in time does not mean it did; see _TelemetryProvider.shutdown.

        Bounding the wait does not bound the work. The default executor's
        threads are joined at interpreter exit, so a wedged export would
        block process teardown even though close() returned. What actually
        caps it is the deadline _TelemetryProvider.shutdown puts on the write
        RPC; do not remove that.
        """
        providers = list(self._telemetry_providers.values())
        self._telemetry_providers.clear()
        self._metric_recorders.clear()
        flushed = True
        if providers:
            loop = asyncio.get_running_loop()
            try:
                # return_exceptions, because a bare gather propagates the
                # first failure immediately and leaves the other providers
                # flushing on executor threads with nothing waiting on them.
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *[
                            loop.run_in_executor(None, provider.shutdown)
                            for provider in providers
                        ],
                        return_exceptions=True,
                    ),
                    timeout=TELEMETRY_SHUTDOWN_TIMEOUT_S,
                )
            except asyncio.TimeoutError as e:
                # A write may still be in flight, which is what decides
                # whether the channel can be closed below. A provider that
                # raised has finished; one that returned False has not, and is
                # handled with the results.
                flushed = False
                logger.debug(f"Built-in metrics failed to shut down cleanly: {e}")
            else:
                for result in results:
                    if isinstance(result, BaseException):
                        logger.debug(
                            f"Built-in metrics failed to shut down cleanly: {result}"
                        )
                    elif result is False:
                        # Returned within the budget, but gave up on a write
                        # that is still going. Same conclusion as a timeout as
                        # far as the channel below is concerned.
                        flushed = False
                        logger.debug(
                            "Built-in metrics gave up on a write still in flight"
                        )
        if self._monitoring_client is not None:
            # Release the gRPC channel opened on the first dial, but only once
            # no export is still using it. An abandoned flush keeps running on
            # its executor thread after the wait above gives up, and closing
            # the channel under its in-flight write would fail the write
            # that the deadline was meant to let finish. Leaving the channel
            # to the garbage collector is the lesser cost; the process is
            # on its way down either way.
            if flushed:
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
    except _UnsupportedExporter as e:
        # Warn rather than debug: the dependency range is open, so an
        # incompatible release can reach users before it reaches CI, and
        # falling back quietly would drop every metric with no signal.
        # Bounded by _get_telemetry_provider, which caches the resulting
        # NullTelemetryProvider, so this is once per project, not per dial.
        logger.warning(f"Disabling built-in metrics: {e}.")
        return NullTelemetryProvider()
    except Exception as e:
        logger.debug(f"Built-in metrics exporter failed to initialize: {e}")
        return NullTelemetryProvider()
