import os
import pty
import time
import threading
from math import sin, pi
import random
import numpy as np

class DummyConstantTempChamber:
    def __init__(self, error_rate_nak=0, error_rate_unexpected=0, error_rate_disconnect=0, error_rate_noresponse=0, recovery_time=10, time_constant=300):
        # Device state variables
        self.pv1 = 250  # Measured temperature (25.0°C)
        self.sv1 = 370  # Set temperature (37.0°C)
        self.running = False
        self.timer = 0
        self.timer_mode = "AUTO_STOP"
        self.locked = False
        self.run_mode = "FIXED"
        self.baudrate = 4800
        self.stopbits = "2"
        self.data_length = "8"
        self.parity = "NONE"
        self.address = "01"
        self.response_wait_time = 0
        self.access_mode = "ON"

        # Error simulation configuration
        self.error_rate_nak = error_rate_nak
        self.error_rate_unexpected = error_rate_unexpected
        self.error_rate_disconnect = error_rate_disconnect
        self.error_rate_noresponse = error_rate_noresponse
        self.recovery_time = recovery_time

        # Time constant for temperature transition (in seconds)
        self.time_constant = time_constant

        # Internal state for error simulation
        self.disconnect_until = 0
        self.noresponse_until = 0

        self.elapsed_time = 0  # Elapsed time in seconds
        self.target_temp = self.sv1  # Initial target temperature
        self.DT = 0  # Difference between target temperature and current temperature
        self.T0 = 0  # Initial current temperature when setting changes
        self.last_update_time = 0  # Last time the target temperature changed

        # Symbolic link to dummy tty
        self.master_fd, slave = pty.openpty()
        self.slave_path = os.ttyname(slave)
        self.link_path  = "/tmp/virtual_yamatobb301"

        if os.path.exists(self.link_path):
            os.remove(self.link_path)
        os.symlink(self.slave_path, self.link_path)
        print(f"Use this fixed device file: {self.link_path} -> {self.slave_path}")
        

    def record_settings_change(self):
        # Record the current temperature (T0) and temperature difference (DT) when settings change
        self.T0 = self.pv1  # Record current temperature
        self.DT = self.target_temp - self.pv1  # Record the difference to target temperature
        self.last_update_time = self.elapsed_time  # Store the time of the update

    def update(self):
        # Simulate temperature change over time.
        self.elapsed_time += 1

        # Handle disconnection and reconnection within the update loop
        self.simulate_disconnect()

        # Check if the temperature target has changed (either SV1 or RUN status)
        if self.running:
            target_temp = self.sv1  # When RUN is active, use set temperature (SV1)
        else:
            target_temp = 225  # When RUN is not active, use default temperature (D)

        # If target temperature has changed, record the time and temperature difference
        if self.target_temp != target_temp:
            self.target_temp = target_temp
            self.record_settings_change()  # Record the change in settings

        # Calculate time difference since the settings change
        time_diff = self.elapsed_time - self.last_update_time

        # Directly update the current temperature based on the exponential decay model
        self.pv1 = self.T0 + self.DT * (1 - np.exp(-time_diff / self.time_constant))

        # Add sinusoidal variation on top of the temperature
        cycle_sec = 600
        amplitude = 2
        theta = 2 * pi * (self.elapsed_time % cycle_sec) / cycle_sec
        sinusoidal_variation = amplitude * sin(theta)

        # Calculate the final temperature with sinusoidal variation
        final_temperature = self.pv1 + sinusoidal_variation

        # Print all the information in one line
        print(f"RUN: {self.running}, SV1: {self.sv1}, Target Temperature: {target_temp/10.:.1f}, Current Median: {self.pv1/10.:.1f}, Temperature with sinusoidal variation: {final_temperature/10.:.1f}")
        
        # Update the temperature with sinusoidal variation
        self.pv1 = final_temperature

    def simulate_disconnect(self):
        # Check if the symbolic link exists
        if os.path.exists(self.link_path):
            # Simulate disconnection based on error rate
            if random.randint(1, 100) <= self.error_rate_disconnect:
                os.remove(self.link_path)  # Remove the symbolic link to simulate disconnection
                print("Simulating disconnection by removing the symbolic link.")
                self.disconnect_until = time.time() + self.recovery_time  # Set recovery time

        # If the symbolic link doesn't exist and recovery time has passed, restore the link
        elif time.time() >= self.disconnect_until:
            os.symlink(self.slave_path, self.link_path)  # Restore the symbolic link
            print("Restoring device by creating the symbolic link.")
            self.disconnect_until = 0  # Reset the disconnect timer

    def handle_command(self, identifier, value=None):
        # Handle simulated device commands, with error simulation.
        # Simulate no response based on error_rate_noresponse
        if random.randint(1, 100) <= self.error_rate_noresponse:
            print("Simulating no response.")
            return None  # Simulate no response for this command

        # Simulate NAK error
        if random.randint(1, 100) <= self.error_rate_nak:
            print("Simulating NAK response.")
            return "\x15" + "NAK00"

        # Simulate unexpected response
        if random.randint(1, 100) <= self.error_rate_unexpected:
            print("Simulating unexpected response.")
            return "???"

        # Normal command handling
        if identifier == "PV1":
            return f"{int(self.pv1):05d}"
        if identifier == "SV1":
            self.sv1 = int(value)
            return ""
        if identifier == "RUN":
            self.running = (value == "00001")
            return ""
        if identifier == "TIM":
            self.timer = int(value)
            return ""
        if identifier == "TMS":
            self.timer_mode = value
            return ""
        if identifier == "LOC":
            self.locked = (value == "00001")
            return ""
        if identifier == "RST":
            self.run_mode = value
            return ""
        if identifier == "BPS":
            self.baudrate = int(value)
            return ""
        if identifier == "SPB":
            self.stopbits = value
            return ""
        if identifier == "DAT":
            self.data_length = value
            return ""
        if identifier == "PAL":
            self.parity = value
            return ""
        if identifier == "ADR":
            self.address = value
            return ""
        if identifier == "AWT":
            self.response_wait_time = int(value)
            return ""
        if identifier == "MOD":
            self.access_mode = value
            return ""
        if identifier in ["OM1", "ER1", "ER2", "BCC", " TI"]:
            return "00000"

        return "00000"

def calculate_bcc(data: bytes) -> bytes:
    # Calculate Block Check Character (BCC) by XORing all bytes.
    bcc = 0
    for byte in data:
        bcc ^= byte
    return bytes([bcc])


def parse_request(data: bytes):
    # Parse incoming data to extract command and value.
    if len(data) < 6:
        return None
    rw_type = data[3:4].decode()
    identifier = data[4:7].decode().strip()
    value = data[7:-2].decode().strip() if rw_type == 'W' else None
    return identifier, rw_type, value


def state_update_loop(chamber):
    # Background thread to update the temperature state over time.
    while True:
        chamber.update()
        time.sleep(1)


def dummy_device_communication_loop(chamber):
    # Main loop for handling simulated serial communication.
    master_fd = chamber.master_fd
    ser = os.fdopen(master_fd, 'rb+', buffering=0)
    while True:
        request = ser.read(256)
        if not request:
            continue
        parsed = parse_request(request)
        if parsed is None:
            continue

        identifier, rw_type, value = parsed

        try:
            response_data = chamber.handle_command(identifier, value)

            if response_data is None:
                # Simulate no response (timeout)
                continue

            response = b'\x02' + b'01' + b'\x06' + identifier.encode().ljust(3) + response_data.encode().ljust(5) + b'\x03'
            response += calculate_bcc(response)

            ser.write(response)

        except OSError as e:
            # Simulate disconnection by closing the serial connection
            ser.close()
            time.sleep(0.5)
            ser = os.fdopen(master_fd, 'rb+', buffering=0)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Dummy Yamato BB301 Temperature Chamber for Testing."
    )
    parser.add_argument("--error-rate-nak",         type=int, default=0,  help="NAK error rate (percentage)")
    parser.add_argument("--error-rate-unexpected",  type=int, default=0,  help="Unexpected response error rate (percentage)")
    parser.add_argument("--error-rate-disconnect",  type=int, default=0,  help="Device disconnection error rate (percentage)")
    parser.add_argument("--error-rate-noresponse",  type=int, default=0,  help="No response error rate (percentage)")
    parser.add_argument("--recovery-time",          type=int, default=10, help="Recovery time for disconnection/no-response (seconds)")
    parser.add_argument("--time-constant",          type=int, default=50, help="Time constant for exponential temperature transition (seconds)")

    args = parser.parse_args()

    chamber = DummyConstantTempChamber(
        error_rate_nak=args.error_rate_nak,
        error_rate_unexpected=args.error_rate_unexpected,
        error_rate_disconnect=args.error_rate_disconnect,
        error_rate_noresponse=args.error_rate_noresponse,
        recovery_time=args.recovery_time,
        time_constant=args.time_constant  # Use time_constant from the arguments
    )

    state_thread = threading.Thread(target=state_update_loop, args=(chamber,), daemon=True)
    state_thread.start()

    dummy_device_communication_loop(chamber)

