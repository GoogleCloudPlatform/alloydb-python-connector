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
alloydb.googleapis.com/client/connector metric prefix.

The telemetry is enabled by default and can be disabled via the
``enable_builtin_telemetry`` option on the Connector or AsyncConnector.

Architecture
------------
A single ``_TelemetryProvider`` is created per Connector (lazily on first
connect, once the project ID is known). It owns the OTel ``MeterProvider``,
``PeriodicExportingMetricReader``, exporter, and shared instruments. This
means **one background export thread** per Connector regardless of how many
instances it connects to.

Each instance gets a lightweight ``_MetricRecorder`` that holds pre-built
attribute dicts and a reference to the shared instruments. Instance identity
(project, location, cluster, instance, client_uid) is carried as metric
attributes on every data point. At export time, the custom exporter moves
these from metric labels to the monitored resource labels on each time
series so that Cloud Monitoring associates them with the correct
``alloydb.googleapis.com/InstanceClient`` resource.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from typing import Optional
from typing import Union

logger = logging.getLogger(__name__)

_PYTHON_CONNECTOR = "python"

# Meter name — matches Go connector.
_METER_NAME = "alloydb.googleapis.com/client/connector"
# Monitored resource type — matches Go connector.
_MONITORED_RESOURCE = "alloydb.googleapis.com/InstanceClient"

# Metric names.
_DIAL_COUNT = "dial_count"
_DIAL_LATENCY = "dial_latencies"
_OPEN_CONNECTIONS = "open_connections"
_BYTES_SENT = "bytes_sent_count"
_BYTES_RECEIVED = "bytes_received_count"
_REFRESH_COUNT = "refresh_count"

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


class NullTelemetryProvider:
    """A no-op TelemetryProvider for when telemetry is disabled."""

    def shutdown(self) -> None:
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


class _TelemetryProvider:
    """Owns a single MeterProvider shared across all instances.

    Created once per Connector (lazily on first connect). Holds the OTel
    MeterProvider, PeriodicExportingMetricReader (one background thread),
    and the shared instrument objects (counters, histogram).

    Call ``create_metric_recorder`` to get a lightweight per-instance
    ``_MetricRecorder`` that records to the shared instruments with
    instance-specific attributes.
    """

    def __init__(
        self,
        project_id: str,
        client_uid: str,
        version: str,
        monitoring_client: object,
    ) -> None:
        from opentelemetry.exporter.cloud_monitoring import (
            CloudMonitoringMetricsExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        class _SystemMetricsExporter(CloudMonitoringMetricsExporter):
            """Exporter that extracts instance labels from metric attributes
            and sets them as monitored resource labels on each time series."""

            def _batch_write(self, series: Any) -> None:
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
                super()._batch_write(series)

            def _get_metric_descriptor(self, metric: Any) -> Any:
                descriptor_type = f"{self._prefix}/{metric.name}"
                if descriptor_type in self._metric_descriptors:
                    return self._metric_descriptors[descriptor_type]

                from opentelemetry.sdk.metrics.export import Sum

                from google.api import metric_pb2

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

        resource = Resource.create({_RESOURCE_TYPE_KEY: _MONITORED_RESOURCE})

        exporter = _SystemMetricsExporter(
            project_id=project_id,
            client=monitoring_client,
            prefix="alloydb.googleapis.com/client/connector",
        )

        # Suppress noisy ERROR logs from the exporter when Cloud Monitoring
        # is not enabled or the caller lacks permissions.
        _exporter_logger = logging.getLogger("opentelemetry.exporter.cloud_monitoring")
        if not logger.isEnabledFor(logging.DEBUG):
            _exporter_logger.setLevel(logging.CRITICAL)

        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=_DEFAULT_EXPORT_INTERVAL_MS,
        )

        self._provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )

        meter = self._provider.get_meter(
            _METER_NAME,
            version=version,
        )

        self._dial_count = meter.create_counter(_DIAL_COUNT)
        self._dial_latency = meter.create_histogram(_DIAL_LATENCY)
        self._open_connections = meter.create_up_down_counter(_OPEN_CONNECTIONS)
        self._bytes_tx = meter.create_counter(_BYTES_SENT)
        self._bytes_rx = meter.create_counter(_BYTES_RECEIVED)
        self._refresh_count = meter.create_counter(_REFRESH_COUNT)

        self._client_uid = client_uid

    def shutdown(self) -> None:
        self._provider.shutdown()

    def create_metric_recorder(
        self,
        project_id: str,
        location: str,
        cluster: str,
        instance: str,
    ) -> _MetricRecorder:
        """Create a lightweight MetricRecorder for a specific instance."""
        return _MetricRecorder(
            provider=self,
            project_id=project_id,
            location=location,
            cluster=cluster,
            instance=instance,
            client_uid=self._client_uid,
        )


class _MetricRecorder:
    """Lightweight per-instance recorder that delegates to shared instruments.

    Created by ``_TelemetryProvider.create_metric_recorder``. Holds pre-built
    attribute dicts that include both metric attributes (connector_type, etc.)
    and resource-identifying labels (project, location, cluster, instance,
    client_uid). The exporter's ``_batch_write`` moves the resource labels
    from metric labels to the monitored resource on each time series.

    Hot-path methods (``record_bytes_rx``, ``record_bytes_tx``) use cached
    dicts to avoid per-call allocation.
    """

    def __init__(
        self,
        provider: _TelemetryProvider,
        project_id: str,
        location: str,
        cluster: str,
        instance: str,
        client_uid: str,
    ) -> None:
        self._p = provider

        # Resource-identifying labels included in every metric data point.
        resource_labels = {
            _PROJECT_ID: project_id,
            _LOCATION: location,
            _CLUSTER_ID: cluster,
            _INSTANCE_ID: instance,
            _CLIENT_UID: client_uid,
        }

        # Pre-build attribute dicts for hot-path methods to avoid
        # allocating a new dict on every socket recv/send call.
        self._bytes_attrs = {
            _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
            **resource_labels,
        }
        self._latency_attrs = {
            _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
            **resource_labels,
        }

        # Base attrs for methods that add dynamic keys per call.
        self._resource_labels = resource_labels

    def record_dial_count(self, attrs: TelemetryAttributes) -> None:
        self._p._dial_count.add(
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
        self._p._dial_latency.record(latency_ms, self._latency_attrs)

    def record_open_connection(self, attrs: TelemetryAttributes) -> None:
        self._p._open_connections.add(
            1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                **self._resource_labels,
            },
        )

    def record_closed_connection(self, attrs: TelemetryAttributes) -> None:
        self._p._open_connections.add(
            -1,
            {
                _CONNECTOR_TYPE: _PYTHON_CONNECTOR,
                _AUTH_TYPE: _auth_type_value(attrs.iam_authn),
                **self._resource_labels,
            },
        )

    def record_bytes_rx(self, count: int) -> None:
        self._p._bytes_rx.add(count, self._bytes_attrs)

    def record_bytes_tx(self, count: int) -> None:
        self._p._bytes_tx.add(count, self._bytes_attrs)

    def record_refresh_count(self, attrs: TelemetryAttributes) -> None:
        self._p._refresh_count.add(
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


def new_telemetry_provider(
    enabled: bool,
    project_id: str,
    client_uid: str,
    version: str,
    monitoring_client: Optional[object] = None,
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
