from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class IntegrationEvent:
    kind: str
    topic: str
    payload: Mapping[str, Any]
    external_id: str
