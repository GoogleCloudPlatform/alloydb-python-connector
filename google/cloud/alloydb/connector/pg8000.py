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
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import ssl

    import pg8000


def connect(sock: "ssl.SSLSocket", **kwargs: Any) -> "pg8000.dbapi.Connection":
    """Create a pg8000 DBAPI connection object.

    Args:
        sock (ssl.SSLSocket): SSL/TLS secure socket stream connected to the
            AlloyDB proxy server.

    Returns:
        pg8000.dbapi.Connection: A pg8000 Connection object for
        the AlloyDB instance.
    """
    try:
        import pg8000
    except ImportError:
        raise ImportError(
            'Unable to import module "pg8000." Please install and try again.'
        )

    user = kwargs.pop("user")
    db = kwargs.pop("db")
    passwd = kwargs.pop("password", None)
    return pg8000.dbapi.connect(
        user,
        database=db,
        password=passwd,
        sock=sock,
        **kwargs,
    )
