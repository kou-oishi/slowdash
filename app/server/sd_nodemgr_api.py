# Created for node_management feature #

import json
import asyncio
import datetime
import logging
from typing import Optional

import slowlette
from sd_component import Component

from sd_nodemgr_state import StateRecord
from sd_nodemgr_alert import AlertRecord
from sd_nodemgr_node import NodeRegistry, Node, HeartbeatMonitor, _DEFAULT_SERIAL
from sd_nodemgr_history import HistoryConfig, HistoryManager


class NodeManagementComponent(Component):
    """Slowlette component implementing the node_management API:
      POST /api/state
      POST /api/alert
      POST /api/heartbeat
      GET  /api/state
      GET  /api/alert
      POST /api/alert/{alert_id}/ack
      POST /api/alert/{alert_id}/close
      GET  /api/node_management/status
    """

    def __init__(self, app, project):
        super().__init__(app, project)

        nm_cfg = project.config.get('node_management', None)
        if nm_cfg is None or not isinstance(nm_cfg, dict):
            self._enabled = False
            self._registry = None
            self._history = None
            self._monitor = None
            return

        self._enabled = True
        self._registry = NodeRegistry(nm_cfg)

        hist_cfg = HistoryConfig(nm_cfg.get('history', {}) or {})
        self._history = HistoryManager(hist_cfg, project.project_dir)

        self._monitor = HeartbeatMonitor(
            self._registry,
            on_offline=self._on_offline,
            interval_seconds=10.0,
        )

        # Store on app so user/task modules can reach it
        app.node_management = self


    # -----------------------------------------------------------------------
    # Lifecycle

    @slowlette.on_event('startup')
    async def startup(self):
        if self._enabled and self._monitor:
            self._monitor.start()
            logging.info('NodeManagement: started')

    @slowlette.on_event('shutdown')
    async def shutdown(self):
        if self._enabled and self._monitor:
            self._monitor.stop()


    # -----------------------------------------------------------------------
    # public_config

    def public_config(self):
        if not self._enabled:
            return {}
        node_ids = [n.node_id for n in self._registry.all_nodes()]
        return {'node_management': {'nodes': node_ids}}


    # -----------------------------------------------------------------------
    # Helper: get client IP from Slowlette request

    @staticmethod
    def _client_ip(request: slowlette.Request) -> str:
        forwarded = request.headers.get('x-forwarded-for', '') if hasattr(request, 'headers') else ''
        if forwarded:
            return forwarded.split(',')[0].strip()
        remote = getattr(request, 'client', None)
        if remote:
            host = remote[0] if isinstance(remote, (list, tuple)) else str(remote)
            return host
        return '127.0.0.1'


    # -----------------------------------------------------------------------
    # POST /api/state

    @slowlette.post('/api/state')
    async def post_state(self, body: slowlette.DictJSON, request: slowlette.Request = None):
        if not self._enabled:
            return None

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        doc = dict(body)

        state_record = StateRecord.from_dict(doc, received_at=now)
        if state_record is None:
            return slowlette.Response(400, content={'error': 'invalid state payload'})

        node = self._registry.get(state_record.id)
        if node is None:
            action = self._registry.check_unregistered(state_record.id)
            if action == 'reject':
                return slowlette.Response(403, content={'error': f'unknown node: {state_record.id}'})
            if action == 'warn':
                logging.warning(f'NodeManagement: state from unregistered node: {state_record.id}')
            return slowlette.Response(200, content={'result': 'ok'})

        # IP check
        if request is not None:
            client_ip = self._client_ip(request)
            serial_id_for_ip = state_record.serial_id
            if not self._registry.is_ip_allowed(node, client_ip, serial_id_for_ip):
                logging.warning(f'NodeManagement: forbidden IP {client_ip} for node {state_record.id}')
                return slowlette.Response(403, content={'error': 'forbidden'})

        # Resolve serial
        internal_serial = node.resolve_serial(state_record.serial_id)
        if internal_serial is None:
            return slowlette.Response(400, content={'error': 'serial_id required for multi-serial node'})

        # heartbeat_only check
        serial_cfg = node.get_serial_config(internal_serial)
        is_hb_only = serial_cfg.get('heartbeat_only', node.heartbeat_only)
        if is_hb_only:
            return slowlette.Response(400, content={'error': 'this target is heartbeat_only; use /api/heartbeat'})

        # Normalise serial_id in record
        if internal_serial != _DEFAULT_SERIAL:
            state_record.serial_id = internal_serial
        else:
            state_record.serial_id = None

        old_state = node.update_state(state_record, internal_serial)
        node.update_heartbeat(internal_serial, now)

        # Callback to owner module/task
        await self._dispatch_on_state_update(node, internal_serial, state_record)

        # PubSub publish
        if old_state is None or old_state.state != state_record.state:
            await self._publish_state_changed(node, internal_serial, state_record, old_state)

        # History
        self._history.record_state(state_record)

        return {'result': 'ok'}


    # -----------------------------------------------------------------------
    # GET /api/state

    @slowlette.get('/api/state')
    async def get_state(self, id: str = None, serial_id: str = None):
        if not self._enabled:
            return None

        if id is not None:
            node = self._registry.get(id)
            if node is None:
                return slowlette.Response(404, content={'error': 'unknown node'})
            if serial_id:
                internal = node.resolve_serial(serial_id)
                if internal is None:
                    return slowlette.Response(404, content={'error': 'unknown serial'})
                sr = node.get_state(internal)
                ls = node.get_last_seen(internal)
                d = sr.to_dict() if sr else {'id': id, 'serial_id': serial_id}
                if ls:
                    d['last_seen'] = ls.strftime('%Y-%m-%dT%H:%M:%SZ')
                return d
            return node.to_status_dict()

        # return all nodes
        return [n.to_status_dict() for n in self._registry.all_nodes()]


    # -----------------------------------------------------------------------
    # POST /api/alert

    @slowlette.post('/api/alert')
    async def post_alert(self, body: slowlette.DictJSON, request: slowlette.Request = None):
        if not self._enabled:
            return None

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        doc = dict(body)

        alert_record = AlertRecord.from_dict(doc, received_at=now)
        if alert_record is None:
            return slowlette.Response(400, content={'error': 'invalid alert payload'})

        node = self._registry.get(alert_record.id)
        if node is None:
            action = self._registry.check_unregistered(alert_record.id)
            if action == 'reject':
                return slowlette.Response(403, content={'error': f'unknown node: {alert_record.id}'})
            logging.warning(f'NodeManagement: alert from unregistered node: {alert_record.id}')
            return slowlette.Response(200, content={'result': 'ok'})

        # IP check
        if request is not None:
            client_ip = self._client_ip(request)
            if not self._registry.is_ip_allowed(node, client_ip, alert_record.serial_id):
                return slowlette.Response(403, content={'error': 'forbidden'})

        # Resolve serial
        internal_serial = node.resolve_serial(alert_record.serial_id)
        if internal_serial is None:
            return slowlette.Response(400, content={'error': 'serial_id required for multi-serial node'})

        # Normalise serial_id
        if internal_serial != _DEFAULT_SERIAL:
            alert_record.serial_id = internal_serial
        else:
            alert_record.serial_id = None

        alert_record.assign_id()
        is_new = node.add_alert(alert_record, internal_serial)

        # History + PubSub for new alerts only
        if is_new:
            self._history.record_alert(alert_record, 'open')
            await self._publish_alert_event('alert.open', alert_record)

        # Callback to owner
        await self._dispatch_on_alert_update(node, internal_serial, alert_record)

        status_code = 201 if is_new else 200
        return slowlette.Response(status_code, content={'alert_id': alert_record.alert_id})


    # -----------------------------------------------------------------------
    # GET /api/alert

    @slowlette.get('/api/alert')
    async def get_alert(self, id: str = None, serial_id: str = None):
        if not self._enabled:
            return None

        result = []
        nodes = [self._registry.get(id)] if id else self._registry.all_nodes()
        for node in nodes:
            if node is None:
                continue
            for a in node.get_all_open_alerts():
                if serial_id and a.serial_id != serial_id:
                    continue
                result.append(a.to_dict())
        return result


    # -----------------------------------------------------------------------
    # POST /api/alert/{alert_id}/ack

    @slowlette.post('/api/alert/{alert_id}/ack')
    async def ack_alert(self, alert_id: str):
        if not self._enabled:
            return None

        for node in self._registry.all_nodes():
            a = node.ack_alert(alert_id)
            if a is not None:
                self._history.record_alert(a, 'ack')
                await self._publish_alert_event('alert.ack', a)
                await self._dispatch_on_alert_update(node, self._alert_internal_serial(node, a), a)
                return {'result': 'ok', 'alert_id': alert_id}

        return slowlette.Response(404, content={'error': 'alert not found'})


    # -----------------------------------------------------------------------
    # POST /api/alert/{alert_id}/close

    @slowlette.post('/api/alert/{alert_id}/close')
    async def close_alert(self, alert_id: str):
        if not self._enabled:
            return None

        for node in self._registry.all_nodes():
            a = node.close_alert(alert_id)
            if a is not None:
                self._history.record_alert(a, 'close')
                await self._publish_alert_event('alert.close', a)
                await self._dispatch_on_alert_update(node, self._alert_internal_serial(node, a), a)
                return {'result': 'ok', 'alert_id': alert_id}

        return slowlette.Response(404, content={'error': 'alert not found'})


    # -----------------------------------------------------------------------
    # POST /api/heartbeat

    @slowlette.post('/api/heartbeat')
    async def post_heartbeat(self, body: slowlette.DictJSON, request: slowlette.Request = None):
        if not self._enabled:
            return None

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        doc = dict(body)
        node_id = doc.get('id')
        serial_id = doc.get('serial_id') or None

        if not node_id:
            return slowlette.Response(400, content={'error': 'missing "id"'})

        node = self._registry.get(node_id)
        if node is None:
            action = self._registry.check_unregistered(node_id)
            if action == 'reject':
                return slowlette.Response(403, content={'error': f'unknown node: {node_id}'})
            logging.warning(f'NodeManagement: heartbeat from unregistered node: {node_id}')
            return {'result': 'ok'}

        # IP check
        if request is not None:
            client_ip = self._client_ip(request)
            if not self._registry.is_ip_allowed(node, client_ip, serial_id):
                return slowlette.Response(403, content={'error': 'forbidden'})

        internal_serial = node.resolve_serial(serial_id)
        if internal_serial is None:
            return slowlette.Response(400, content={'error': 'serial_id required for multi-serial node'})

        node.update_heartbeat(internal_serial, now)

        # PubSub
        client_ip_str = self._client_ip(request) if request else 'unknown'
        await self._publish('heartbeat.received', {
            'id': node_id,
            'serial_id': serial_id,
            'ts': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'src_ip': client_ip_str,
        })

        return {'result': 'ok'}


    # -----------------------------------------------------------------------
    # GET /api/node_management/status  (UI summary)

    @slowlette.get('/api/node_management/status')
    async def get_status(self):
        if not self._enabled:
            return None
        return [n.to_status_dict() for n in self._registry.all_nodes()]


    # -----------------------------------------------------------------------
    # Internal: OFFLINE callback from HeartbeatMonitor

    async def _on_offline(self, node: Node, internal_serial: str):
        state = node.get_state(internal_serial)
        if state:
            await self._publish_state_changed(node, internal_serial, state, None)
            self._history.record_state(state)
        await self._dispatch_on_state_update(node, internal_serial, state)


    # -----------------------------------------------------------------------
    # Internal: owner module/task callback dispatch

    async def _dispatch_on_state_update(self, node: Node, internal_serial: str, state_record: Optional[StateRecord]):
        owner = getattr(node, '_owner', None)
        if owner is None:
            return
        func = getattr(owner, '_on_state_update', None) or getattr(owner, 'get_func', lambda n: None)('_on_state_update')
        if not callable(func):
            return
        display_serial = None if internal_serial == _DEFAULT_SERIAL else internal_serial
        try:
            import inspect
            if inspect.iscoroutinefunction(func):
                await func(display_serial, state_record)
            else:
                func(display_serial, state_record)
        except Exception as e:
            logging.error(f'NodeManagement: _on_state_update callback error: {e}')

    async def _dispatch_on_alert_update(self, node: Node, internal_serial: str, alert_record: AlertRecord):
        owner = getattr(node, '_owner', None)
        if owner is None:
            return
        func = getattr(owner, '_on_alert_update', None) or getattr(owner, 'get_func', lambda n: None)('_on_alert_update')
        if not callable(func):
            return
        display_serial = None if internal_serial == _DEFAULT_SERIAL else internal_serial
        try:
            import inspect
            if inspect.iscoroutinefunction(func):
                await func(display_serial, alert_record)
            else:
                func(display_serial, alert_record)
        except Exception as e:
            logging.error(f'NodeManagement: _on_alert_update callback error: {e}')


    # -----------------------------------------------------------------------
    # Internal: PubSub helpers

    async def _publish(self, topic: str, data: dict):
        """Publish to the pubsub component via the app."""
        pubsub = getattr(self.app, '_pubsub_component', None)
        if pubsub is not None:
            try:
                await pubsub.server_publish(topic, data)
            except Exception as e:
                logging.debug(f'NodeManagement: pubsub publish error: {e}')

    async def _publish_state_changed(self, node: Node, internal_serial: str,
                                      new_state: StateRecord, old_state: Optional[StateRecord]):
        display_serial = None if internal_serial == _DEFAULT_SERIAL else internal_serial
        await self._publish('state.changed', {
            'id': node.node_id,
            'serial_id': display_serial,
            'field': 'state',
            'old': old_state.state if old_state else None,
            'new': new_state.state,
            'ts': new_state.ts.strftime('%Y-%m-%dT%H:%M:%SZ') if new_state.ts else None,
        })

    async def _publish_alert_event(self, topic: str, alert: AlertRecord):
        await self._publish(topic, {
            'alert_id': alert.alert_id,
            'id': alert.id,
            'serial_id': alert.serial_id,
            'level': alert.level,
            'msg': alert.msg,
            'ts': alert.ts.strftime('%Y-%m-%dT%H:%M:%SZ') if alert.ts else None,
        })

    @staticmethod
    def _alert_internal_serial(node: Node, alert: AlertRecord) -> str:
        if alert.serial_id and alert.serial_id in node._serials:
            return alert.serial_id
        return _DEFAULT_SERIAL


    # -----------------------------------------------------------------------
    # emit_alert: called by owner module/task to raise an alert from server side

    async def emit_alert(self, node_id: str, alert_record: AlertRecord) -> Optional[str]:
        """Allow owner module/task to raise an alert.
        Returns the alert_id (existing if duplicate, new if created).
        """
        if not self._enabled:
            return None
        node = self._registry.get(node_id)
        if node is None:
            logging.warning(f'NodeManagement.emit_alert: unknown node: {node_id}')
            return None

        internal_serial = node.resolve_serial(alert_record.serial_id)
        if internal_serial is None:
            logging.warning(f'NodeManagement.emit_alert: serial_id required for multi-serial node {node_id}')
            return None

        if internal_serial != _DEFAULT_SERIAL:
            alert_record.serial_id = internal_serial
        else:
            alert_record.serial_id = None

        alert_record.assign_id()
        is_new = node.add_alert(alert_record, internal_serial)

        if is_new:
            self._history.record_alert(alert_record, 'open')
            await self._publish_alert_event('alert.open', alert_record)

        return alert_record.alert_id
