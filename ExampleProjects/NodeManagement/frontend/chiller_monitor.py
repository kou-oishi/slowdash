import threading
import time
from typing import Optional
from YamatoBB301 import (
    Controller,
    DeviceNAKException,
    UnexpectedResponseException,
    DeviceConnectionException,
    DeviceNoResponseException,
    DeviceUnknownException,
)


class TemperatureMonitor(threading.Thread):
    def __init__(self, channel, device, interval=20, max_error_duration=1,
                 reporter=None, serial_id=None, use_db=True):
        # Initialise monitor thread and verify device connection
        super().__init__()
        self.channel = channel
        self.device = device
        self.interval = interval
        self.max_error_duration = max_error_duration  # in minutes
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

        # SlowDash Reporter — only set when called from the Frontend
        self.reporter = reporter
        self.serial_id = serial_id  # e.g. "00", "01" …

        # DB writing — disabled in standalone debug mode
        self.use_db = use_db

        # Verify connection during initialisation
        self.controller = Controller(port=self.device)

        # Error state tracking
        self.error_count = 0
        self.error_start_time = None
        self.waiting_mode = False

    def stop(self):
        # Signal the thread to stop
        self.stop_event.set()

    def pause(self):
        # Enter waiting mode (external trigger)
        self.waiting_mode = True
        self.pause_event.clear()

    def resume(self):
        # Resume from waiting mode
        self.waiting_mode = False
        self.pause_event.set()

    # ------------------------------------------------------------------
    # SlowDash reporting helpers (no-ops when reporter is None)
    # ------------------------------------------------------------------

    def _report_state(self, state: str, *, code: Optional[str] = None, msg: Optional[str] = None):
        if self.reporter is not None:
            self.reporter.post_state(state, serial_id=self.serial_id, code=code, msg=msg)

    def _report_alert(self, level: str, msg: str, *, code: Optional[str] = None):
        if self.reporter is not None:
            self.reporter.post_alert(level, msg, serial_id=self.serial_id, code=code)

    def _report_heartbeat(self):
        if self.reporter is not None:
            self.reporter.post_heartbeat(serial_id=self.serial_id)

    # ------------------------------------------------------------------

    def run(self):
        # Periodically read temperature; optionally log to DB and report to SlowDash
        db = None
        if self.use_db:
            from chiller_mysql import ChillerMySQL
            db = ChillerMySQL()

        self._report_state("RUNNING", msg=f"Channel {self.channel} monitoring started")

        try:
            while not self.stop_event.is_set():
                # Heartbeat — every loop iteration (even while waiting)
                self._report_heartbeat()

                # If in waiting mode, block until resume() is called
                if self.waiting_mode:
                    print(f"Channel {self.channel}: Waiting mode. Paused...")
                    self.pause_event.wait()
                    # Resumed — send RUNNING state again
                    self._report_state("RUNNING", msg=f"Channel {self.channel} monitoring resumed")

                try:
                    temperature = self.controller.read_temperature()

                    if db is not None:
                        db.insert_temperature(self.channel, temperature)

                    # Recovery from error state
                    if self.error_count > 0:
                        self._report_state("RUNNING", msg=f"Channel {self.channel} recovered")
                    self.error_count = 0
                    self.error_start_time = None

                    print(f"Channel {self.channel}: Temperature {temperature:.1f} °C")

                except Exception as e:
                    self.error_count += 1

                    if self.error_start_time is None:
                        self.error_start_time = time.time()
                        # First error: send WARNING state + alert
                        self._report_state(
                            "WARNING",
                            code=type(e).__name__,
                            msg=f"Channel {self.channel}: {e}",
                        )
                        self._report_alert(
                            "warning",
                            f"Channel {self.channel} read error: {e}",
                            code=type(e).__name__,
                        )

                    error_duration = (time.time() - self.error_start_time) / 60.0

                    if db is not None:
                        db.insert_error(
                            self.channel,
                            type(e).__name__,
                            f"[MON] {e} (continuous occurrence: {self.error_count}, duration: {error_duration:.1f} min)"
                        )
                    print(f"Channel {self.channel}: ERROR {type(e).__name__} - {e} (count: {self.error_count}, duration: {error_duration:.1f} min)")

                    if error_duration >= self.max_error_duration:
                        if db is not None:
                            db.insert_error(
                                self.channel,
                                "WAITING_MODE",
                                f"[MON] Entered waiting mode after {self.error_count} errors over {error_duration:.1f} minutes"
                            )
                        print(f"Channel {self.channel}: WAITING_MODE after {self.error_count} errors in {error_duration:.1f} minutes")

                        self._report_state(
                            "DISABLED",
                            code="WAITING_MODE",
                            msg=f"Channel {self.channel}: entered waiting mode after {self.error_count} errors over {error_duration:.1f} min",
                        )
                        self._report_alert(
                            "error",
                            f"Channel {self.channel} entered waiting mode: {self.error_count} errors over {error_duration:.1f} min",
                            code="WAITING_MODE",
                        )
                        self.pause()

                time.sleep(self.interval)

        finally:
            if db is not None:
                db.close()

    @staticmethod
    def validate_and_register_channel(channel, device, register_db=True):
        # Validate device connection; optionally register channel in database
        try:
            controller = Controller(port=device)
            temperature = controller.read_temperature()
            print(f"Channel {channel} ({device}) check OK: {temperature:.1f} °C")
        except DeviceConnectionException as e:
            raise RuntimeError(f"Channel {channel} validation failed: {e}")

        if register_db:
            from chiller_mysql import ChillerMySQL
            db = ChillerMySQL()
            db.ensure_channel_registered(channel)
            db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Chiller Temperature Monitor — standalone debug mode. "
                    "SQL writing is disabled by default. SlowDash reporting is not available here; "
                    "run via chiller_frontend.py for full integration."
    )
    parser.add_argument("--channel",            type=int, required=True, help="Chiller channel number (must be >= 0)")
    parser.add_argument("--device",             type=str, required=True, help="Serial device path")
    parser.add_argument("--interval",           type=int, default=5,     help="Interval (sec) between measurements")
    parser.add_argument("--max-error-duration", type=int, default=1,     help="Max error duration (min) before entering waiting mode")
    parser.add_argument("--sql",                action="store_true",     help="Enable SQL writing to database (off by default in standalone mode)")

    args = parser.parse_args()

    if args.channel < 0:
        parser.error("Channel number must be >= 0")

    try:
        TemperatureMonitor.validate_and_register_channel(args.channel, args.device, register_db=args.sql)

        monitor = TemperatureMonitor(
            channel=args.channel,
            device=args.device,
            interval=args.interval,
            max_error_duration=args.max_error_duration,
            use_db=args.sql,
        )
        monitor.start()

        print(f"Monitoring channel {args.channel} (device: {args.device})"
              f"{' [SQL enabled]' if args.sql else ' [SQL disabled — debug mode]'}")

        monitor.join()

    except KeyboardInterrupt:
        print("Stopping monitor...")
        monitor.stop()
        monitor.join()

    except RuntimeError as e:
        print(e)
