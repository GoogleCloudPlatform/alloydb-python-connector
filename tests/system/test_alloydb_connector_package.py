# Copyright 2025 Google LLC
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

import google.cloud.alloydb.connector as conn_old
import google.cloud.alloydb_connector as conn_new


def test_alloydb_connector_package() -> None:
    """
    Test imported objects are same in google.cloud.alloydb.connector and
    google.cloud.alloydb_connector packages.
    """
    assert conn_old.AsyncConnector == conn_new.AsyncConnector
    assert conn_old.Connector == conn_new.Connector
    assert conn_old.IPTypes == conn_new.IPTypes
    assert conn_old.RefreshStrategy == conn_new.RefreshStrategy
    assert conn_old.__version__ == conn_new.__version__
