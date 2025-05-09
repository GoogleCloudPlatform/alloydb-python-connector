# Copyright 2024 Google LLC
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

from __future__ import annotations

from enum import Enum


class IPTypes(Enum):
    """
    Enum for specifying IP type to connect to AlloyDB with.
    """

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    PSC = "PSC"

    @classmethod
    def _missing_(cls, value: object) -> None:
        raise ValueError(
            f"Incorrect value for ip_type, got '{value}'. Want one of: "
            f"{', '.join([repr(m.value) for m in cls])}."
        )


class RefreshStrategy(Enum):
    """
    Enum for specifying refresh strategy to connect to AlloyDB with.
    """

    LAZY = "LAZY"
    BACKGROUND = "BACKGROUND"

    @classmethod
    def _missing_(cls, value: object) -> None:
        raise ValueError(
            f"Incorrect value for refresh_strategy, got '{value}'. Want one of: "
            f"{', '.join([repr(m.value) for m in cls])}."
        )
