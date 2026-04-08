# Created for node_management feature #

import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional, Any


VALID_STATES = ['OFFLINE', 'DISABLED', 'READY', 'RUNNING', 'FAULT', 'STARTING', 'WARNING', 'TRIPPED']

# Severity ordering for min_state filtering (OFFLINE is excluded - it's handled separately)
_SEVERITY_ORDER = ['READY', 'RUNNING', 'STARTING', 'DISABLED', 'WARNING', 'TRIPPED', 'FAULT']
_SEVERITY_MAP = {s: i for i, s in enumerate(_SEVERITY_ORDER)}


def parse_ts(ts) -> Optional[datetime.datetime]:
    """Parse a timestamp value to a UTC datetime.
    Accepts: int (Unix time), float (Unix time), str (ISO 8601), datetime, or None.
    Returns a timezone-aware UTC datetime, or None on failure.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=datetime.timezone.utc)
        return ts.astimezone(datetime.timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.datetime.fromtimestamp(float(ts), tz=datetime.timezone.utc)
    if isinstance(ts, str):
        for fmt in [
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%S.%f%z',
            '%Y-%m-%d %H:%M:%S',
        ]:
            try:
                dt = datetime.datetime.strptime(ts, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt.astimezone(datetime.timezone.utc)
            except ValueError:
                continue
    logging.warning(f'StateRecord: cannot parse timestamp: {repr(ts)}')
    return None


@dataclass
class StateRecord:
    """Canonical representation of a state update from a Frontend.
    Corresponds to the JSON payload of POST /api/state.
    """
    id: str
    state: str
    serial_id: Optional[str] = None
    raw_state: Optional[str] = None
    ts: Optional[datetime.datetime] = None        # UTC datetime
    code: Optional[str] = None
    msg: Optional[str] = None
    data: Optional[Any] = None

    @classmethod
    def from_dict(cls, doc: dict, received_at: Optional[datetime.datetime] = None) -> Optional['StateRecord']:
        """Build a StateRecord from a request payload dict.
        Returns None if required fields are missing or state is invalid.
        """
        node_id = doc.get('id')
        state = doc.get('state')
        if not node_id or not state:
            logging.warning('StateRecord: missing required field "id" or "state"')
            return None
        if state not in VALID_STATES:
            logging.warning(f'StateRecord: invalid state value: {repr(state)}')
            return None

        ts_raw = doc.get('ts')
        if ts_raw is not None:
            ts = parse_ts(ts_raw)
        else:
            ts = received_at if received_at is not None else datetime.datetime.now(tz=datetime.timezone.utc)

        return cls(
            id=node_id,
            state=state,
            serial_id=doc.get('serial_id') or None,
            raw_state=doc.get('raw_state') or None,
            ts=ts,
            code=doc.get('code') or None,
            msg=doc.get('msg') or None,
            data=doc.get('data'),
        )

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        d = {
            'id': self.id,
            'state': self.state,
        }
        if self.serial_id is not None:
            d['serial_id'] = self.serial_id
        if self.raw_state is not None:
            d['raw_state'] = self.raw_state
        if self.ts is not None:
            d['ts'] = self.ts.strftime('%Y-%m-%dT%H:%M:%SZ')
        if self.code is not None:
            d['code'] = self.code
        if self.msg is not None:
            d['msg'] = self.msg
        if self.data is not None:
            d['data'] = self.data
        return d

    @staticmethod
    def severity(state: str) -> int:
        """Return the severity integer for min_state filtering.
        OFFLINE is not in this ordering; use is_offline() separately.
        """
        return _SEVERITY_MAP.get(state, -1)

    @staticmethod
    def is_above_min(state: str, min_state: str) -> bool:
        """Return True if state's severity >= min_state's severity.
        OFFLINE is always excluded from this comparison.
        """
        if state == 'OFFLINE':
            return False
        if min_state not in _SEVERITY_MAP:
            return True
        return _SEVERITY_MAP.get(state, -1) >= _SEVERITY_MAP[min_state]
