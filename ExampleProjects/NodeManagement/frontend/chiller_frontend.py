import socket
import sys
import threading
from YamatoBB301 import (
    Controller, TimerMode, OperationType, ParityMode, AccessMode,
    DeviceNAKException, UnexpectedResponseException,
    DeviceConnectionException, DeviceNoResponseException, DeviceUnknownException,
)
from chiller_monitor import TemperatureMonitor
from chiller_mysql import ChillerMySQL


class CommandExecutionException(Exception):
    def __init__(self, message):
        super().__init__(message)


DEFAULT_PORT = 15000  # Default TCP port


class ChillerFrontend:
    def __init__(self, port, interval=5, reporter=None):
        # Frontend initialisation
        self.port = port
        self.interval = interval
        self.channel_ports = {}
        self.monitors = {}
        self.reporter = reporter  # SlowDashReporter (optional)

    def _serial_id(self, channel: int) -> str:
        return f"{channel:02d}"

    def _validate_and_register_channel(self, channel, device):
        # Validate connection and register channel in database
        try:
            controller = Controller(port=device)
            temperature = controller.read_temperature()
            print(f"Channel {channel} ({device}) check OK: {temperature:.1f} °C")
        except DeviceConnectionException as e:
            # Critical: channel is unreachable at startup
            if self.reporter is not None:
                self.reporter.post_alert(
                    "critical",
                    f"Channel {channel} ({device}) failed to initialise: {e}",
                    serial_id=self._serial_id(channel),
                    code="INIT_FAILED",
                )
            raise RuntimeError(f"Channel {channel} validation failed: {e}")

        db = ChillerMySQL()
        db.ensure_channel_registered(channel)
        db.close()

    def handle_monitor_command(self, channel, action, conn, db):
        # Handle pause and resume monitor commands
        monitor = self.monitors.get(channel)

        if not monitor:
            conn.sendall(f"ERROR: No monitor running for channel {channel}".encode('utf-8'))
            return

        if action == "pause":
            monitor.pause()
            conn.sendall(f"Channel {channel} monitor paused.".encode('utf-8'))
        elif action == "resume":
            monitor.resume()
            conn.sendall(f"Channel {channel} monitor resumed.".encode('utf-8'))
        else:
            conn.sendall(f"ERROR: Invalid monitor action '{action}'".encode('utf-8'))
            return

        # COMMAND LOGGING
        db.insert_history(channel, f"{action}_monitor", None, None)

    def handle_device_command(self, channel, command, conn, db):
        # Execute command on the chiller and handle exceptions
        parts = command.strip().split()
        if not parts:
            raise CommandExecutionException(f"Empty command received for channel {channel}")

        method_name = parts[0]
        args = parts[1:]

        try:
            serial_port = self.channel_ports[channel]
            controller = Controller(port=serial_port)

            method = getattr(controller, method_name)
            result = method(*[Controller.cast_argument(arg) for arg in args])
            result_str = str(result) if result is not None else "OK"

            # Record successful command execution
            db.insert_history(
                channel, method_name,
                " ".join(args) if args else None,
                result_str if result not in ["OK", ""] else None,
            )

            conn.sendall(result_str.encode('utf-8'))

        except AttributeError:
            # Unknown command — inform the client only, not worth an alert
            raise CommandExecutionException(f"Unknown command '{method_name}' for channel {channel}")

        except (DeviceNAKException, DeviceNoResponseException,
                DeviceUnknownException, UnexpectedResponseException,
                DeviceConnectionException) as e:
            # Device-level error — log to DB and raise a SlowDash alert
            db.insert_error(channel, type(e).__name__, f"[CTRL] {e} (command: {command})")
            if self.reporter is not None:
                self.reporter.post_alert(
                    "warning",
                    f"Channel {channel} command error ({method_name}): {e}",
                    serial_id=self._serial_id(channel),
                    code=type(e).__name__,
                )
            raise CommandExecutionException(
                f"Execution failed for channel {channel} — command: {command}, error: {e}"
            )

        except Exception as e:
            # ERROR LOGGING
            db.insert_error(channel, type(e).__name__, f"[CTRL] {e} (command: {command})")
            raise CommandExecutionException(
                f"Execution failed for channel {channel} — command: {command}, error: {e}"
            )

    def handle_client(self, conn, addr):
        # Handle individual TCP client session
        try:
            data = conn.recv(1024)
            if not data:
                conn.sendall(b"ERROR: No data received")
                return

            command = data.decode('utf-8').strip()
            print(f"Received command: {command} from {addr}")

            if command.lower() == 'help':
                conn.sendall(self.print_tcp_help().encode('utf-8'))
                return

            parts = command.split(maxsplit=1)

            try:
                channel = int(parts[0])
                if channel < 0:
                    raise ValueError()
            except (ValueError, IndexError):
                conn.sendall(b"ERROR: Invalid channel number")
                return

            if channel not in self.channel_ports:
                conn.sendall(f"ERROR: Channel {channel} not configured".encode('utf-8'))
                return

            command_body = parts[1].strip() if len(parts) > 1 else ""

            db = ChillerMySQL()

            if command_body in ["pause_monitor", "resume_monitor"]:
                self.handle_monitor_command(channel, command_body.split("_")[0], conn, db)
            else:
                self.handle_device_command(channel, command_body, conn, db)

            db.close()

        except Exception as e:
            conn.sendall(f"ERROR: {type(e).__name__}: {str(e)}".encode('utf-8'))

    def start_tcp_server(self):
        # Start the TCP server and listen for connections
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('0.0.0.0', self.port))
            server.listen(1)
            print(f"Listening on port {self.port}...")

            while True:
                conn, addr = server.accept()
                with conn:
                    self.handle_client(conn, addr)

    def initialise_channels(self, channel_device_mappings):
        # Validate and launch all monitor modules, then start the TCP server
        for mapping in channel_device_mappings:
            channel, device = mapping.split(':', 1)
            channel = int(channel)
            if channel < 0:
                raise ValueError(f"Channel number must be non-negative: {channel}")

            self._validate_and_register_channel(channel, device)

            serial_id = self._serial_id(channel)

            monitor = TemperatureMonitor(
                channel,
                device,
                interval=self.interval,
                reporter=self.reporter,
                serial_id=serial_id,
            )
            monitor.start()
            self.monitors[channel] = monitor
            self.channel_ports[channel] = device

            # Channel ready — report READY state to SlowDash
            if self.reporter is not None:
                self.reporter.post_state(
                    "READY",
                    serial_id=serial_id,
                    msg=f"Channel {channel} ({device}) initialised OK",
                )

    @staticmethod
    def print_tcp_help():
        # Print TCP usage guide with available commands
        return f"""\
TCP Usage Guide:
  - Specify the channel number followed by the command.
  - Format: <channel> <command> [args...]
  - For testing, use 'nc' (netcat) as a TCP client:

    Example (server on localhost, port {DEFAULT_PORT}):
      $ echo "0 read_temperature" | nc 127.0.0.1 {DEFAULT_PORT}
      $ echo "0 set_temperature 25.5" | nc 127.0.0.1 {DEFAULT_PORT}
      $ echo "0 set_timer_mode AUTO_STOP" | nc 127.0.0.1 {DEFAULT_PORT}

  - Monitor control:
      $ echo "0 pause_monitor" | nc 127.0.0.1 {DEFAULT_PORT}
      $ echo "0 resume_monitor" | nc 127.0.0.1 {DEFAULT_PORT}

  - Help:
      $ echo "help" | nc 127.0.0.1 {DEFAULT_PORT}

""" + Controller.help()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="YamatoBB301 Chiller Frontend")
    parser.add_argument('--port',         type=int,  default=DEFAULT_PORT, help='TCP port to listen on')
    parser.add_argument('--interval',     type=int,  default=20,           help='Temperature monitoring interval (seconds)')
    parser.add_argument('--slowdash-url', type=str,  default=None,         help='SlowDash URL for state/alert/heartbeat reporting (e.g. http://localhost:18881)')
    parser.add_argument('--node-id',      type=str,  default='chiller',    help='SlowDash node ID for this Frontend')
    parser.add_argument('channel_ports',  nargs='+',                       help='Channel to port mapping, e.g. 0:/dev/ttyUSB0 1:/dev/ttyUSB1')

    args = parser.parse_args()

    reporter = None
    if args.slowdash_url:
        from sd_reporter import SlowDashReporter
        reporter = SlowDashReporter(args.slowdash_url, args.node_id)
        print(f"SlowDash reporting enabled: {args.slowdash_url} (node: {args.node_id})")

    frontend = ChillerFrontend(args.port, args.interval, reporter=reporter)

    try:
        frontend.initialise_channels(args.channel_ports)
        frontend.start_tcp_server()

    except RuntimeError as e:
        print(e)
        sys.exit(1)

    except KeyboardInterrupt:
        print("\nStopping all monitors...")
        for monitor in frontend.monitors.values():
            monitor.stop()
        for monitor in frontend.monitors.values():
            monitor.join()

        # Notify SlowDash that each channel has gone offline cleanly
        if reporter is not None:
            for channel in frontend.channel_ports:
                reporter.post_state(
                    "OFFLINE",
                    serial_id=frontend._serial_id(channel),
                    code="SHUTDOWN",
                    msg=f"Channel {channel} stopped cleanly",
                )

        print("All monitors stopped. Exiting.")
