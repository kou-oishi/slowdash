"""
chiller.py  —  node_management owner module for YamatoBB301 chiller Frontend.

SlowDash の node_management.nodes.chiller.module として動作する。
- _initialize / _process_command / _halt : SlowDash UserModule インターフェース
- _on_state_update / _on_alert_update    : node_management コールバック (任意)
- emit_alert()                           : サーバー側 alert 発行 (UserModule 基底経由)

_node_id および _serials は SlowDash から params 経由で注入される。
"""
import re
import socket

# ─────────────────────────────────────────
# Default config (params で上書きされる)
# ─────────────────────────────────────────
TCP_HOST = "localhost"
TCP_PORT = 15000

def _initialize(params):
    global TCP_HOST, TCP_PORT
    TCP_HOST = params.get('tcp_host', TCP_HOST)
    TCP_PORT = int(params.get('tcp_port', TCP_PORT))

    # node_management から注入されるパラメータ (あれば記録)
    node_id = params.get('_node_id')
    serials  = params.get('_serials', [])
    print(f"[chiller] Initialized: host={TCP_HOST}, port={TCP_PORT}, node_id={node_id}, serials={serials}")


def _process_command(doc):
    """
    SlowDash コントロールパネルから送られてくるコマンドドキュメントを処理する。

    キー形式: "<command>_<NN>" (例: start_00, set_temperature_00)
    値が True のキーを実行対象とし、"<NN> <command> [value]" を TCP 送信する。
    """
    try:
        pattern = re.compile(r'^(.+)_([0-9]{2})$')
        for key, flag in doc.items():
            if flag:
                m = pattern.match(key)
                if m:
                    cmd     = m.group(1)
                    channel = int(m.group(2))
                    cmd_value = doc.get(f"{key}_value", None)

                    with socket.create_connection((TCP_HOST, TCP_PORT), timeout=3) as s:
                        send_cmd = f"{channel} {cmd}"
                        if cmd_value is not None:
                            send_cmd += f" {cmd_value}"
                        s.sendall(send_cmd.encode('utf-8'))
                        response = s.recv(1024).decode('utf-8')
                        return True

    except Exception as e:
        return {"status": "error", "message": str(e)}

    return False  # Nothing executed


def _on_state_update(serial_id, state_record):
    """
    Frontend から /api/state が POST されたときに SlowDash が呼び出すコールバック。

    Parameters
    ----------
    serial_id : str | None
        変化があったチャンネル ("00", "01", …)、または None
    state_record : StateRecord
        新しい State の情報 (state_record.state, .msg, .code 等)
    """
    state = state_record.state
    msg   = getattr(state_record, 'msg', '')
    print(f"[chiller] State update — serial={serial_id}, state={state}, msg={msg}")

def _on_alert_update(serial_id, alert_record):
    """
    alert の open / ack / close 時に SlowDash が呼び出すコールバック。

    Parameters
    ----------
    serial_id : str | None
        対象チャンネル
    alert_record : AlertRecord
        アラート情報 (level, msg, status 等)
    """
    level  = alert_record.level
    msg    = alert_record.msg
    status = alert_record.status
    print(f"[chiller] Alert update — serial={serial_id}, level={level}, status={status}, msg={msg}")


def _halt():
    print("[chiller] Module halted.")
