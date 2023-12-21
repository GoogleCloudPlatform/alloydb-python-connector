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

from datetime import datetime, timedelta, timezone
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mocks import FakeInstance

from google.cloud.alloydb.connector.refresh import (
    _seconds_until_refresh,
    RefreshResult,
)


def test_seconds_until_refresh_over_1_hour() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is over 1 hour, should return duration/2.
    """
    now = datetime.now()
    assert _seconds_until_refresh(now + timedelta(minutes=62), now) == 31 * 60


def test_seconds_until_refresh_under_1_hour_over_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 1 hour and over 4 minutes,
    should return duration-refresh_buffer (refresh_buffer = 4 minutes).
    """
    now = datetime.now(timezone.utc)
    assert _seconds_until_refresh(now + timedelta(minutes=5), now) == 60


def test_seconds_until_refresh_under_4_mins() -> None:
    """
    Test _seconds_until_refresh returns proper time in seconds.
    If expiration is under 4 minutes, should return 0.
    """
    assert (
        _seconds_until_refresh(datetime.now(timezone.utc) + timedelta(minutes=3)) == 0
    )


def test_RefreshResult_init_(fake_instance: FakeInstance) -> None:
    """
    Test to check whether the __init__ method of RefreshResult
    can correctly initialize TLS context.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake_instance.generate_certs()
    root_cert, intermediate_cert, ca_cert = fake_instance.get_pem_certs()
    # build client cert
    client_cert = (
        x509.CertificateBuilder()
        .subject_name(fake_instance.intermediate_cert.subject)
        .issuer_name(fake_instance.intermediate_cert.issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(minutes=10))
    )
    # sign client cert with intermediate cert
    client_cert = client_cert.sign(fake_instance.intermediate_key, hashes.SHA256())
    client_cert = client_cert.public_bytes(encoding=serialization.Encoding.PEM).decode(
        "UTF-8"
    )
    certs = (ca_cert, [client_cert, intermediate_cert, root_cert])
    refresh = RefreshResult(fake_instance.ip_address, key, certs)
    # verify TLS requirements
    assert refresh.context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert refresh.context.request_ssl is False
