"""
SlowDashReporter: Frontend から SlowDash の node_management API へ
State/Alert/Heartbeat を POST するための HTTP クライアント。
stdlib (urllib) のみ使用、エラーは握りつぶす（非同期通信の失敗は許容）。
"""
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class SlowDashReporter:
    """
    SlowDash の /api/state, /api/alert, /api/heartbeat に HTTP POST するクライアント。

    Parameters
    ----------
    base_url : str
        SlowDash の URL (例: "http://localhost:18881")
    node_id : str
        このFrontend が対応する SlowDash node の ID
    timeout : float
        HTTP リクエストのタイムアウト秒数
    """

    def __init__(self, base_url: str, node_id: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.node_id = node_id
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if body:
                    return json.loads(body)
                return {}
        except urllib.error.HTTPError as e:
            logger.warning("SlowDashReporter: HTTP %d for %s", e.code, url)
        except urllib.error.URLError as e:
            logger.debug("SlowDashReporter: URLError for %s: %s", url, e.reason)
        except Exception as e:
            logger.debug("SlowDashReporter: unexpected error for %s: %s", url, e)
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post_state(
        self,
        state: str,
        serial_id: Optional[str] = None,
        *,
        code: Optional[str] = None,
        msg: Optional[str] = None,
        data: Optional[dict] = None,
        ts: Optional[str] = None,
    ) -> bool:
        """
        /api/state に POST する。

        Parameters
        ----------
        state : str
            "READY", "RUNNING", "WARNING", "DISABLED", "FAULT" など
        serial_id : str, optional
            複数 serial がある場合はチャンネル文字列 ("00", "01" …)
        code : str, optional
            状態コード (任意)
        msg : str, optional
            状態メッセージ (任意)
        data : dict, optional
            追加データ (任意)
        ts : str, optional
            ISO 8601 タイムスタンプ。省略時は現在時刻

        Returns
        -------
        bool
            POST 成功なら True
        """
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()

        payload: dict = {"id": self.node_id, "state": state, "ts": ts}
        if serial_id is not None:
            payload["serial_id"] = serial_id
        if code is not None:
            payload["code"] = code
        if msg is not None:
            payload["msg"] = msg
        if data is not None:
            payload["data"] = data

        result = self._post("/api/state", payload)
        return result is not None

    def post_alert(
        self,
        level: str,
        msg: str,
        serial_id: Optional[str] = None,
        *,
        code: Optional[str] = None,
        meta: Optional[dict] = None,
        lifecycle: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> Optional[str]:
        """
        /api/alert に POST する。

        Parameters
        ----------
        level : str
            "info", "warning", "error", "critical"
        msg : str
            アラートメッセージ
        serial_id : str, optional
            対象チャンネル
        code : str, optional
            アラートコード (任意)
        meta : dict, optional
            追加メタデータ (任意)
        lifecycle : str, optional
            "open", "ack", "close" (省略時はサーバーのデフォルト)
        ts : str, optional
            ISO 8601 タイムスタンプ。省略時は現在時刻

        Returns
        -------
        Optional[str]
            サーバーが返した alert_id、失敗時は None
        """
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()

        payload: dict = {"id": self.node_id, "level": level, "msg": msg, "ts": ts}
        if serial_id is not None:
            payload["serial_id"] = serial_id
        if code is not None:
            payload["code"] = code
        if meta is not None:
            payload["meta"] = meta
        if lifecycle is not None:
            payload["lifecycle"] = lifecycle

        result = self._post("/api/alert", payload)
        if result is None:
            return None
        return result.get("alert_id")

    def post_heartbeat(self, serial_id: Optional[str] = None, *, ts: Optional[str] = None) -> bool:
        """
        /api/heartbeat に POST する。

        Parameters
        ----------
        serial_id : str, optional
            対象チャンネル (複数 serial がある場合)
        ts : str, optional
            ISO 8601 タイムスタンプ。省略時は現在時刻

        Returns
        -------
        bool
            POST 成功なら True
        """
        if ts is None:
            ts = datetime.now(timezone.utc).isoformat()

        payload: dict = {"id": self.node_id, "ts": ts}
        if serial_id is not None:
            payload["serial_id"] = serial_id

        result = self._post("/api/heartbeat", payload)
        return result is not None
