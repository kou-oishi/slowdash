# Created for node_management feature #

import ipaddress
import asyncio
import datetime
import logging
from typing import Optional, Dict, List, Any

from sd_nodemgr_state import StateRecord
from sd_nodemgr_alert import AlertRecord


# Internal sentinel serial used for nodes that define no serials
_DEFAULT_SERIAL = '__node__'


class SerialState:
    """Runtime state for a single serial (or node-level when no serials defined)."""

    def __init__(self, serial_id: str, config: dict):
        self.serial_id = serial_id          # the real serial_id (or _DEFAULT_SERIAL)
        self.config = config                # per-serial config dict from yaml
        self.current_state: Optional[StateRecord] = None
        self.open_alerts: List[AlertRecord] = []
        self.last_seen: Optional[datetime.datetime] = None
        self.is_offline: bool = False       # True when heartbeat timed out


class Node:
    """Runtime management object for a single node.

    Holds State / Alert / heartbeat per serial (or node-level when no serials).
    For nodes with no serials a virtual serial _DEFAULT_SERIAL is used internally.
    """

    def __init__(self, node_id: str, config: dict):
        self.node_id = node_id
        self.config = config

        # Map: real_serial_id (or _DEFAULT_SERIAL) -> SerialState
        self._serials: Dict[str, SerialState] = {}
        self._owner = None   # set by NodeManagementComponent after module/task starts

        # Resolve configured serials
        serials_cfg = config.get('serials', {}) or {}
        if serials_cfg:
            for sid, scfg in serials_cfg.items():
                self._serials[sid] = SerialState(sid, scfg or {})
        else:
            self._serials[_DEFAULT_SERIAL] = SerialState(_DEFAULT_SERIAL, {})

    # ------------------------------------------------------------------
    # Properties

    @property
    def label(self) -> str:
        return self.config.get('label', self.node_id)

    @property
    def heartbeat_only(self) -> bool:
        return bool(self.config.get('heartbeat_only', False))

    @property
    def heartbeat_grace_seconds(self) -> int:
        return int(self.config.get('heartbeat_grace_seconds', 180))

    @property
    def allowed_ips(self) -> Optional[List[str]]:
        return self.config.get('allowed_ips') or None

    @property
    def has_multiple_serials(self) -> bool:
        return len(self._serials) > 1 or (
            len(self._serials) == 1 and _DEFAULT_SERIAL not in self._serials
        )

    def real_serial_ids(self) -> List[str]:
        """Return the list of real (user-defined) serial_ids, or [] if node has no serials."""
        return [s for s in self._serials if s != _DEFAULT_SERIAL]

    # ------------------------------------------------------------------
    # Routing

    def resolve_serial(self, serial_id: Optional[str]) -> Optional[str]:
        """Resolve an incoming serial_id to an internal key.

        Rules (section 4.3 of spec):
          - serial_id given: must match a known serial (or _DEFAULT_SERIAL for ownerless nodes)
          - serial_id absent:
            - no real serials: -> _DEFAULT_SERIAL
            - one real serial: -> that serial (implicit mapping)
            - multiple serials: -> None (caller should return 400)
        """
        if serial_id:
            if serial_id in self._serials:
                return serial_id
            return None  # unknown serial

        real = self.real_serial_ids()
        if not real:
            return _DEFAULT_SERIAL
        if len(real) == 1:
            return real[0]
        return None  # ambiguous: multiple serials, 400 required

    def get_serial_config(self, internal_serial: str) -> dict:
        ss = self._serials.get(internal_serial)
        return ss.config if ss else {}

    # ------------------------------------------------------------------
    # IP filtering (section 5.4)

    def is_ip_allowed(self, client_ip: str, serial_id: Optional[str] = None) -> bool:
        """Check whether client_ip is permitted to post to this node/serial.
        Priority: serial.allowed_ips > node.allowed_ips > (global, checked by caller).
        Returns True if no restriction is configured at this level.
        """
        # Determine the effective allowed_ips list
        allowed = None
        if serial_id and serial_id in self._serials:
            allowed = self._serials[serial_id].config.get('allowed_ips') or None
        if allowed is None:
            allowed = self.allowed_ips

        if allowed is None:
            return True   # no restriction at node/serial level; caller checks global

        return _ip_in_list(client_ip, allowed)

    # ------------------------------------------------------------------
    # State update

    def update_state(self, state_record: StateRecord, internal_serial: str) -> Optional[StateRecord]:
        """Apply a StateRecord to the given internal serial.
        Returns the old StateRecord (or None if first update).
        """
        ss = self._serials.get(internal_serial)
        if ss is None:
            return None
        old = ss.current_state
        ss.current_state = state_record
        ss.is_offline = (state_record.state == 'OFFLINE')
        return old

    def get_state(self, internal_serial: str) -> Optional[StateRecord]:
        ss = self._serials.get(internal_serial)
        return ss.current_state if ss else None

    def set_offline(self, internal_serial: str):
        """Mark a serial as OFFLINE (heartbeat timeout)."""
        ss = self._serials.get(internal_serial)
        if ss is None:
            return
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        sr = StateRecord(
            id=self.node_id,
            state='OFFLINE',
            serial_id=None if internal_serial == _DEFAULT_SERIAL else internal_serial,
            ts=now,
            msg='heartbeat timeout',
        )
        ss.current_state = sr
        ss.is_offline = True

    # ------------------------------------------------------------------
    # Heartbeat

    def update_heartbeat(self, internal_serial: str, received_at: datetime.datetime):
        ss = self._serials.get(internal_serial)
        if ss is None:
            return
        ss.last_seen = received_at
        ss.is_offline = False

    def get_last_seen(self, internal_serial: str) -> Optional[datetime.datetime]:
        ss = self._serials.get(internal_serial)
        return ss.last_seen if ss else None

    def is_heartbeat_timed_out(self, internal_serial: str) -> bool:
        ss = self._serials.get(internal_serial)
        if ss is None or ss.last_seen is None:
            return False
        grace = self.heartbeat_grace_seconds
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return (now - ss.last_seen).total_seconds() > grace

    # ------------------------------------------------------------------
    # Alert management

    def add_alert(self, alert_record: AlertRecord, internal_serial: str) -> bool:
        """Add an alert to the open list.  Returns True if newly added, False if duplicate."""
        ss = self._serials.get(internal_serial)
        if ss is None:
            return False
        for existing in ss.open_alerts:
            if alert_record.is_duplicate_of(existing):
                alert_record.alert_id = existing.alert_id
                return False  # duplicate reused
        ss.open_alerts.append(alert_record)
        return True

    def get_open_alerts(self, internal_serial: str) -> List[AlertRecord]:
        ss = self._serials.get(internal_serial)
        return list(ss.open_alerts) if ss else []

    def get_all_open_alerts(self) -> List[AlertRecord]:
        result = []
        for ss in self._serials.values():
            result.extend(ss.open_alerts)
        return result

    def ack_alert(self, alert_id: str) -> Optional[AlertRecord]:
        """Mark the alert as acknowledged. Returns the AlertRecord or None if not found."""
        for ss in self._serials.values():
            for a in ss.open_alerts:
                if a.alert_id == alert_id:
                    a.status = 'ack'
                    return a
        return None

    def close_alert(self, alert_id: str) -> Optional[AlertRecord]:
        """Close an alert and remove it from the open list. Returns the AlertRecord or None."""
        for ss in self._serials.values():
            for a in list(ss.open_alerts):
                if a.alert_id == alert_id:
                    a.status = 'close'
                    ss.open_alerts.remove(a)
                    return a
        return None

    # ------------------------------------------------------------------
    # Summary for GET /api/state

    def to_status_dict(self) -> dict:
        """Return a summary dict for all serials (used in API responses and UI)."""
        serials_out = {}
        real = self.real_serial_ids()

        for internal_sid, ss in self._serials.items():
            display_sid = None if internal_sid == _DEFAULT_SERIAL else internal_sid
            entry = {}
            if ss.current_state:
                entry['state'] = ss.current_state.state
                if ss.current_state.raw_state:
                    entry['raw_state'] = ss.current_state.raw_state
                if ss.current_state.ts:
                    entry['ts'] = ss.current_state.ts.strftime('%Y-%m-%dT%H:%M:%SZ')
                if ss.current_state.data is not None:
                    entry['data'] = ss.current_state.data
            if ss.last_seen:
                entry['last_seen'] = ss.last_seen.strftime('%Y-%m-%dT%H:%M:%SZ')
            entry['open_alert_count'] = len(ss.open_alerts)
            if ss.open_alerts:
                most_severe = max(ss.open_alerts, key=lambda a: AlertRecord.level_severity(a.level))
                entry['top_alert'] = {'level': most_severe.level, 'msg': most_severe.msg}

            key = display_sid if display_sid else '__node__'
            serials_out[key] = entry

        result = {
            'id': self.node_id,
            'label': self.label,
        }
        if real:
            result['serials'] = serials_out
        else:
            # Flatten single-serial (node-level) state to the top level
            node_entry = serials_out.get('__node__', {})
            result.update(node_entry)

        return result


# ---------------------------------------------------------------------------
# Node Registry

class NodeRegistry:
    """Maps node_id -> Node and enforces global policies."""

    def __init__(self, config: dict):
        """
        config: the 'node_management' section from SlowdashProject.yaml
        """
        self._nodes: Dict[str, Node] = {}
        self._global_allowed_ips: Optional[List[str]] = None
        self._unregistered_behavior: str = 'reject'

        policies = config.get('policies', {}) or {}
        self._unregistered_behavior = policies.get('unregistered_behavior', 'reject')
        self._global_allowed_ips = policies.get('global_allowed_ips') or None

        nodes_cfg = config.get('nodes', {}) or {}
        self._validate(nodes_cfg)

        for node_id, node_cfg in nodes_cfg.items():
            self._nodes[node_id] = Node(node_id, node_cfg or {})

        logging.info(f'NodeRegistry: registered {len(self._nodes)} node(s): {list(self._nodes.keys())}')

    @staticmethod
    def _validate(nodes_cfg: dict):
        """Startup validation (spec §14.1)."""
        errors = []

        # node ids are unique (guaranteed by dict keys, but check for aliasing)
        seen_ids = set()
        for node_id in nodes_cfg:
            if node_id in seen_ids:
                errors.append(f'duplicate node id: {node_id!r}')
            seen_ids.add(node_id)

        for node_id, node_cfg in nodes_cfg.items():
            if not isinstance(node_cfg, dict):
                continue

            # module and task cannot coexist
            has_module = isinstance(node_cfg.get('module'), dict)
            has_task = isinstance(node_cfg.get('task'), dict)
            if has_module and has_task:
                errors.append(f'node {node_id!r}: cannot define both "module" and "task"')

            # serial_ids within a node must be unique
            serials = node_cfg.get('serials') or {}
            if isinstance(serials, dict):
                seen = set()
                for sid in serials:
                    if sid in seen:
                        errors.append(f'node {node_id!r}: duplicate serial_id: {sid!r}')
                    seen.add(sid)

            # allowed_ips must be valid IP/CIDR
            for scope, cfg in [('node', node_cfg)] + [(f'serial {s}', v) for s, v in (node_cfg.get('serials') or {}).items() if isinstance(v, dict)]:
                for entry in (cfg.get('allowed_ips') or []):
                    try:
                        if '/' in str(entry):
                            ipaddress.ip_network(str(entry), strict=False)
                        else:
                            ipaddress.ip_address(str(entry))
                    except ValueError:
                        errors.append(f'node {node_id!r} {scope}: invalid allowed_ips entry: {entry!r}')

        for msg in errors:
            logging.error(f'NodeManagement validation error: {msg}')
        if errors:
            logging.error(f'NodeManagement: {len(errors)} validation error(s) found')

    def get(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def all_nodes(self) -> List[Node]:
        return list(self._nodes.values())

    def is_node_known(self, node_id: str) -> bool:
        return node_id in self._nodes

    def check_unregistered(self, node_id: str) -> str:
        """Return 'ok', 'warn', or 'reject' for an unknown node_id."""
        b = self._unregistered_behavior
        if b == 'accept':
            return 'ok'
        if b == 'warn':
            return 'warn'
        return 'reject'

    def is_ip_allowed_global(self, client_ip: str) -> bool:
        """Check global IP allowlist. Returns True if no global restriction."""
        if self._global_allowed_ips is None:
            return True
        return _ip_in_list(client_ip, self._global_allowed_ips)

    def is_ip_allowed(self, node: Node, client_ip: str, serial_id: Optional[str] = None) -> bool:
        """Full IP check: serial -> node -> global."""
        # Node/serial level
        if not node.is_ip_allowed(client_ip, serial_id):
            return False
        # If node has no restriction, check global
        serial_allowed = node.get_serial_config(serial_id or _DEFAULT_SERIAL).get('allowed_ips') if serial_id else None
        node_allowed = node.allowed_ips
        if serial_allowed is None and node_allowed is None:
            return self.is_ip_allowed_global(client_ip)
        return True


# ---------------------------------------------------------------------------
# Heartbeat monitor (background asyncio task)

class HeartbeatMonitor:
    """Periodically checks heartbeat timeouts and marks nodes OFFLINE."""

    def __init__(self, registry: NodeRegistry, on_offline=None, interval_seconds: float = 10.0):
        """
        on_offline: async callable(node, internal_serial) called when a timeout is detected
        """
        self._registry = registry
        self._on_offline = on_offline
        self._interval = interval_seconds
        self._task: Optional[asyncio.Task] = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self._check()
        except asyncio.CancelledError:
            pass

    async def _check(self):
        for node in self._registry.all_nodes():
            for internal_sid in node._serials:
                ss = node._serials[internal_sid]
                if ss.last_seen is None:
                    continue
                if ss.is_offline:
                    continue
                if node.is_heartbeat_timed_out(internal_sid):
                    logging.info(f'HeartbeatMonitor: OFFLINE: {node.node_id}/{internal_sid}')
                    node.set_offline(internal_sid)
                    if self._on_offline:
                        try:
                            await self._on_offline(node, internal_sid)
                        except Exception as e:
                            logging.error(f'HeartbeatMonitor: on_offline callback error: {e}')


# ---------------------------------------------------------------------------
# Helpers

def _ip_in_list(client_ip: str, allowed: List[str]) -> bool:
    """Return True if client_ip matches any entry in the allowed list (IP or CIDR)."""
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        logging.warning(f'NodeManagement: cannot parse client IP: {repr(client_ip)}')
        return False
    for entry in allowed:
        try:
            if '/' in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if addr == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            logging.warning(f'NodeManagement: bad allowed_ips entry: {repr(entry)}')
    return False
