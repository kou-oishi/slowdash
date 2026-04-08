# Created for node_management feature #

import os
import datetime
import json
import logging
from typing import Optional

from sd_nodemgr_state import StateRecord
from sd_nodemgr_alert import AlertRecord


class HistoryConfig:
    """Parsed node_management.history configuration."""

    def __init__(self, config: dict):
        self.directory: Optional[str] = config.get('directory') or None
        self.use_db: Optional[str] = config.get('use_db') or None

        sh = config.get('state_history', {}) or {}
        self.state_enabled: bool = sh.get('enabled', True)
        self.state_min_state: Optional[str] = sh.get('min_state') or None
        self.state_store_offline: bool = sh.get('store_offline', False)

        ah = config.get('alert_history', {}) or {}
        self.alert_enabled: bool = ah.get('enabled', True)
        self.alert_min_level: Optional[str] = ah.get('min_level') or None


# ---------------------------------------------------------------------------
# Text backend

class TextHistoryBackend:
    """Append-only text file history backend.

    Log paths:
      <base_dir>/YYYYMM/state_YYYYMMDD.log
      <base_dir>/YYYYMM/alert_YYYYMMDD.log
    """

    def __init__(self, base_dir: str):
        self._base = base_dir

    def _log_path(self, record_type: str, ts: datetime.datetime) -> str:
        month_dir = os.path.join(self._base, ts.strftime('%Y%m'))
        return os.path.join(month_dir, f'{record_type}_{ts.strftime("%Y%m%d")}.log')

    def _append(self, path: str, line: str):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception as e:
            logging.error(f'HistoryBackend: cannot write {path}: {e}')

    def write_state(self, state_record: StateRecord):
        ts = state_record.ts or datetime.datetime.now(tz=datetime.timezone.utc)
        path = self._log_path('state', ts)
        entry = {
            'event_ts': ts.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'event_type': 'update',
            'node_id': state_record.id,
            'serial_id': state_record.serial_id,
            'state': state_record.state,
            'raw_state': state_record.raw_state,
            'code': state_record.code,
            'msg': state_record.msg,
        }
        if state_record.data is not None:
            entry['data'] = state_record.data
        self._append(path, json.dumps(entry, ensure_ascii=False))

    def write_alert(self, alert_record: AlertRecord, event_type: str):
        """event_type: 'open' | 'ack' | 'close'"""
        ts = alert_record.ts or datetime.datetime.now(tz=datetime.timezone.utc)
        path = self._log_path('alert', ts)
        entry = {
            'event_ts': ts.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'event_type': event_type,
            'node_id': alert_record.id,
            'serial_id': alert_record.serial_id,
            'alert_id': alert_record.alert_id,
            'level': alert_record.level,
            'lifecycle': alert_record.get_required_lifecycle(),
            'code': alert_record.code,
            'msg': alert_record.msg,
        }
        if alert_record.meta is not None:
            entry['meta'] = alert_record.meta
        self._append(path, json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# SQL backend

def _parse_db_uri(uri: str):
    """Parse a DB URI of the form:
      mysql://user:pass@host:port/db_name[/table_name]
      postgresql://user:pass@host:port/db_name[/table_name]
    Returns (driver, user, password, host, port, db_name, table_name).
    table_name defaults to 'node_management_history'.
    """
    DEFAULT_TABLE = 'node_management_history'
    DEFAULT_PORTS = {'mysql': 3306, 'postgresql': 5432}

    if '://' not in uri:
        return None
    scheme, rest = uri.split('://', 1)
    driver = scheme.lower()
    if driver not in ('mysql', 'postgresql'):
        return None

    # user:pass@host:port/db[/table]
    if '@' in rest:
        userinfo, hostpart = rest.rsplit('@', 1)
    else:
        userinfo, hostpart = '', rest

    user, password = '', ''
    if ':' in userinfo:
        user, password = userinfo.split(':', 1)
    else:
        user = userinfo

    # host:port/db[/table]
    parts = hostpart.split('/')
    hostport = parts[0]
    db_name = parts[1] if len(parts) > 1 else ''
    table_name = parts[2] if len(parts) > 2 else DEFAULT_TABLE

    port = DEFAULT_PORTS.get(driver, 3306)
    if ':' in hostport:
        host, port_str = hostport.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            host = hostport
    else:
        host = hostport

    return driver, user, password, host, port, db_name, table_name


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    record_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    record_type   VARCHAR(16)  NOT NULL,
    event_type    VARCHAR(16)  NOT NULL,
    event_ts      DATETIME     NOT NULL,
    node_id       VARCHAR(256) NOT NULL,
    serial_id     VARCHAR(256),
    state         VARCHAR(32),
    raw_state     VARCHAR(256),
    alert_id      VARCHAR(64),
    level         VARCHAR(16),
    lifecycle     VARCHAR(16),
    code          VARCHAR(256),
    msg           TEXT,
    data_json     TEXT,
    meta_json     TEXT
)
"""

_CREATE_TABLE_SQL_PG = """
CREATE TABLE IF NOT EXISTS {table} (
    record_id     BIGSERIAL PRIMARY KEY,
    record_type   VARCHAR(16)  NOT NULL,
    event_type    VARCHAR(16)  NOT NULL,
    event_ts      TIMESTAMP    NOT NULL,
    node_id       VARCHAR(256) NOT NULL,
    serial_id     VARCHAR(256),
    state         VARCHAR(32),
    raw_state     VARCHAR(256),
    alert_id      VARCHAR(64),
    level         VARCHAR(16),
    lifecycle     VARCHAR(16),
    code          VARCHAR(256),
    msg           TEXT,
    data_json     TEXT,
    meta_json     TEXT
)
"""


class SQLHistoryBackend:
    """SQL database history backend (MySQL or PostgreSQL)."""

    def __init__(self, uri: str):
        self._uri = uri
        self._conn = None
        self._driver = None
        self._table = None
        self._ready = False
        self._setup(uri)

    def _setup(self, uri: str):
        parsed = _parse_db_uri(uri)
        if parsed is None:
            logging.error(f'SQLHistoryBackend: cannot parse DB URI: {uri}')
            return
        driver, user, password, host, port, db_name, table_name = parsed
        self._driver = driver
        self._table = table_name

        try:
            if driver == 'mysql':
                import mysql.connector
                self._conn = mysql.connector.connect(
                    host=host, port=port, user=user, password=password, database=db_name,
                    autocommit=True
                )
                self._ensure_table(_CREATE_TABLE_SQL.format(table=table_name))
            elif driver == 'postgresql':
                import psycopg2
                self._conn = psycopg2.connect(
                    host=host, port=port, user=user, password=password, dbname=db_name
                )
                self._conn.autocommit = True
                self._ensure_table(_CREATE_TABLE_SQL_PG.format(table=table_name))
            self._ready = True
            logging.info(f'SQLHistoryBackend: connected ({driver}) table={table_name}')
        except Exception as e:
            logging.error(f'SQLHistoryBackend: connection failed: {e}')

    def _ensure_table(self, ddl: str):
        cur = self._conn.cursor()
        cur.execute(ddl)
        cur.close()

    def _insert(self, record_type, event_type, event_ts, node_id, serial_id,
                state=None, raw_state=None, alert_id=None, level=None,
                lifecycle=None, code=None, msg=None, data_json=None, meta_json=None):
        if not self._ready:
            return
        if self._driver == 'mysql':
            ph = '%s'
        else:
            ph = '%s'
        sql = f"""
            INSERT INTO {self._table}
              (record_type, event_type, event_ts, node_id, serial_id,
               state, raw_state, alert_id, level, lifecycle, code, msg, data_json, meta_json)
            VALUES ({','.join([ph]*14)})
        """
        try:
            cur = self._conn.cursor()
            cur.execute(sql, (
                record_type, event_type, event_ts, node_id, serial_id,
                state, raw_state, alert_id, level, lifecycle, code, msg, data_json, meta_json
            ))
            cur.close()
        except Exception as e:
            logging.error(f'SQLHistoryBackend: insert failed: {e}')

    def write_state(self, state_record: StateRecord):
        ts = state_record.ts or datetime.datetime.now(tz=datetime.timezone.utc)
        self._insert(
            record_type='state',
            event_type='update',
            event_ts=ts.strftime('%Y-%m-%d %H:%M:%S'),
            node_id=state_record.id,
            serial_id=state_record.serial_id,
            state=state_record.state,
            raw_state=state_record.raw_state,
            code=state_record.code,
            msg=state_record.msg,
            data_json=json.dumps(state_record.data) if state_record.data is not None else None,
        )

    def write_alert(self, alert_record: AlertRecord, event_type: str):
        ts = alert_record.ts or datetime.datetime.now(tz=datetime.timezone.utc)
        self._insert(
            record_type='alert',
            event_type=event_type,
            event_ts=ts.strftime('%Y-%m-%d %H:%M:%S'),
            node_id=alert_record.id,
            serial_id=alert_record.serial_id,
            alert_id=alert_record.alert_id,
            level=alert_record.level,
            lifecycle=alert_record.get_required_lifecycle(),
            code=alert_record.code,
            msg=alert_record.msg,
            meta_json=json.dumps(alert_record.meta) if alert_record.meta is not None else None,
        )


# ---------------------------------------------------------------------------
# Combined history manager

class HistoryManager:
    """Writes State/Alert history to text backend and optionally SQL backend.
    The text backend is always active; SQL is optional.
    """

    def __init__(self, config: HistoryConfig, project_dir: Optional[str]):
        self._cfg = config

        base_dir = config.directory
        if base_dir is None and project_dir is not None:
            base_dir = os.path.join(project_dir, 'logs')
        self._text: Optional[TextHistoryBackend] = TextHistoryBackend(base_dir) if base_dir else None

        self._sql: Optional[SQLHistoryBackend] = None
        if config.use_db:
            self._sql = SQLHistoryBackend(config.use_db)

    def record_state(self, state_record: StateRecord):
        """Persist a state update if it meets the configured threshold."""
        if not self._cfg.state_enabled:
            return

        state = state_record.state
        if state == 'OFFLINE':
            if not self._cfg.state_store_offline:
                return
        else:
            if self._cfg.state_min_state and not StateRecord.is_above_min(state, self._cfg.state_min_state):
                return

        if self._text:
            self._text.write_state(state_record)
        if self._sql:
            self._sql.write_state(state_record)

    def record_alert(self, alert_record: AlertRecord, event_type: str):
        """Persist an alert lifecycle event if it meets the configured threshold."""
        if not self._cfg.alert_enabled:
            return
        if self._cfg.alert_min_level:
            if not AlertRecord.is_above_min_level(alert_record.level, self._cfg.alert_min_level):
                return

        if self._text:
            self._text.write_alert(alert_record, event_type)
        if self._sql:
            self._sql.write_alert(alert_record, event_type)
