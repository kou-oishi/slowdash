# Created for node_management feature #

import uuid
import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

from sd_nodemgr_state import parse_ts


VALID_LEVELS = ['info', 'warning', 'error', 'critical']
_LEVEL_SEVERITY = {l: i for i, l in enumerate(VALID_LEVELS)}

VALID_LIFECYCLE = ['open', 'ack', 'close']

# Default required lifecycle stage per alert level
DEFAULT_LIFECYCLE = {
    'info': 'open',
    'warning': 'open',
    'error': 'ack',
    'critical': 'close',
}


@dataclass
class AlertRecord:
    """Canonical representation of an alert.
    Corresponds to the JSON payload of POST /api/alert.

    Fields:
      id         - node id (required)
      level      - 'info' | 'warning' | 'error' | 'critical' (required)
      msg        - human-readable message (required)
      alert_id   - server-assigned unique identifier (set on open)
      serial_id  - physical unit identifier (optional)
      ts         - UTC datetime of the alert (optional, defaults to server receive time)
      lifecycle  - required lifecycle stage: 'open' | 'ack' | 'close'
                   If None, defaults to level-based default (see DEFAULT_LIFECYCLE)
      status     - current lifecycle status: 'open' | 'ack' | 'close'
      code       - machine-readable short identifier (optional)
      meta       - auxiliary information (optional)
    """
    id: str
    level: str
    msg: str
    alert_id: Optional[str] = None
    serial_id: Optional[str] = None
    ts: Optional[datetime.datetime] = None
    lifecycle: Optional[str] = None   # required lifecycle stage
    status: str = 'open'              # current lifecycle status
    code: Optional[str] = None
    meta: Optional[dict] = None

    @classmethod
    def from_dict(cls, doc: dict, received_at: Optional[datetime.datetime] = None) -> Optional['AlertRecord']:
        """Build an AlertRecord from a request payload dict.
        Returns None if required fields are missing or values are invalid.
        """
        node_id = doc.get('id')
        level = doc.get('level')
        msg = doc.get('msg')
        if not node_id:
            logging.warning('AlertRecord: missing required field "id"')
            return None
        if not level or level not in VALID_LEVELS:
            logging.warning(f'AlertRecord: invalid or missing "level": {repr(level)}')
            return None
        if not msg:
            logging.warning('AlertRecord: missing required field "msg"')
            return None

        lifecycle = doc.get('lifecycle')
        if lifecycle is not None and lifecycle not in VALID_LIFECYCLE:
            logging.warning(f'AlertRecord: invalid "lifecycle": {repr(lifecycle)}')
            lifecycle = None

        ts_raw = doc.get('ts')
        if ts_raw is not None:
            ts = parse_ts(ts_raw)
        else:
            ts = received_at if received_at is not None else datetime.datetime.now(tz=datetime.timezone.utc)

        return cls(
            id=node_id,
            level=level,
            msg=msg,
            alert_id=doc.get('alert_id') or None,
            serial_id=doc.get('serial_id') or None,
            ts=ts,
            lifecycle=lifecycle,
            status='open',
            code=doc.get('code') or None,
            meta=doc.get('meta') if isinstance(doc.get('meta'), dict) else None,
        )

    def get_required_lifecycle(self) -> str:
        """Return the required lifecycle stage for this alert.
        Explicit lifecycle takes precedence over level default.
        """
        if self.lifecycle is not None:
            return self.lifecycle
        return DEFAULT_LIFECYCLE.get(self.level, 'open')

    def is_duplicate_of(self, other: 'AlertRecord') -> bool:
        """Return True if this alert is a duplicate of other (for deduplication).
        The other alert must be currently open (status == 'open').
        """
        if other.status != 'open':
            return False
        return (
            self.id == other.id
            and self.serial_id == other.serial_id
            and self.level == other.level
            and self.msg == other.msg
            and self.code == other.code
            and self.get_required_lifecycle() == other.get_required_lifecycle()
        )

    def assign_id(self) -> str:
        """Generate and assign a unique alert_id. Returns the new id."""
        self.alert_id = str(uuid.uuid4())
        return self.alert_id

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        d = {
            'id': self.id,
            'level': self.level,
            'msg': self.msg,
            'status': self.status,
            'lifecycle': self.get_required_lifecycle(),
        }
        if self.alert_id is not None:
            d['alert_id'] = self.alert_id
        if self.serial_id is not None:
            d['serial_id'] = self.serial_id
        if self.ts is not None:
            d['ts'] = self.ts.strftime('%Y-%m-%dT%H:%M:%SZ')
        if self.code is not None:
            d['code'] = self.code
        if self.meta is not None:
            d['meta'] = self.meta
        return d

    @staticmethod
    def level_severity(level: str) -> int:
        """Return the severity integer for min_level filtering."""
        return _LEVEL_SEVERITY.get(level, -1)

    @staticmethod
    def is_above_min_level(level: str, min_level: str) -> bool:
        """Return True if level's severity >= min_level's severity."""
        if min_level not in _LEVEL_SEVERITY:
            return True
        return _LEVEL_SEVERITY.get(level, -1) >= _LEVEL_SEVERITY[min_level]
