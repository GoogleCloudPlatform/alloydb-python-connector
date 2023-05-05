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

import ssl
from typing import Any, TYPE_CHECKING

SERVER_PROXY_PORT = 5433

if TYPE_CHECKING:
    import pg8000


def connect(
    ip_address: str, ctx: ssl.SSLContext, **kwargs: Any
) -> "pg8000.dbapi.Connection":
    """Create a pg8000 DBAPI connection object.

    Args:
        ip_address (str): IP address of AlloyDB instance to connect to.
        ctx (ssl.SSLContext): Context used to create a TLS connection
            with AlloyDB instance ssl certificates.

    Returns:
        pg8000.dbapi.Connection: A pg8000 Connection object for
        the AlloyDB instance.
    """
    # Connecting through pg8000 is done by passing in an SSL Context and setting the
    # "request_ssl" attr to false. This works because when "request_ssl" is false,
    # the driver skips the database level SSL/TLS exchange, but still uses the
    # ssl_context (if it is not None) to create the connection.
    try:
        import pg8000
    except ImportError:
        raise ImportError(
            'Unable to import module "pg8000." Please install and try again.'
        )
    user = kwargs.pop("user")
    db = kwargs.pop("db")
    passwd = kwargs.pop("password")
    setattr(ctx, "request_ssl", False)
    return pg8000.dbapi.connect(
        user,
        database=db,
        password=passwd,
        host=ip_address,
        port=SERVER_PROXY_PORT,
        ssl_context=ctx,
        **kwargs,
    )
