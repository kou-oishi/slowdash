import serial
import time
from enum import Enum

# Override Serial for the dummy chiller
class SerialDummySafe(serial.Serial):
    @property
    def rts(self):
        return False

    @rts.setter
    def rts(self, value):
        pass


class DeviceNAKException(Exception):
    def __init__(self, code: str):
        super().__init__(f"Device returned NAK with code: {code}")
        self.code = code


class UnexpectedResponseException(Exception):
    def __init__(self, identifier: str, data: str):
        super().__init__(f"Unexpected response for {identifier}: {data}")
        self.identifier = identifier
        self.data = data


class DeviceConnectionException(Exception):
    def __init__(self, message: str):
        super().__init__(f"Device connection error: {message}")


class DeviceNoResponseException(Exception):
    def __init__(self):
        super().__init__("Device did not respond.")


class DeviceUnknownException(Exception):
    def __init__(self, message: str):
        super().__init__(f"Unknown device error: {message}")


class TimerMode(Enum):
    AUTO_STOP = "00000"
    AUTO_START = "00001"


class OperationType(Enum):
    FIXED = "00000"
    TIMER = "00001"


class ParityMode(Enum):
    NONE = "N"
    ODD = "O"
    EVEN = "E"


class AccessMode(Enum):
    ON = "RW"
    OFF = "R0"


class Controller:
    def __init__(
        self,
        port,
        baudrate=4800,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_TWO,
        timeout=0.5,
    ):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout

        self.ser_class = serial.Serial if "virtual" not in self.port else SerialDummySafe

        # Validate connection by attempting to read temperature
        try:
            self.read_temperature()
        except Exception as e:
            # No exception detail needed, raise a simple device open error
            raise DeviceConnectionException(f"Failed to open YamatoBB301 device: {self.port}")

    # Function to calculate Block Check Character (BCC)
    def _calculate_bcc(self, data: bytes) -> bytes:
        """Calculates BCC by XORing all bytes (including STX)."""
        bcc = 0
        for byte in data:
            bcc ^= byte
        return bytes([bcc])
        
    def _send_command(self, command, value=None):
        mode = "W" if value is not None else "R"
        
        try:
            with self.ser_class(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
                rtscts=False,
            ) as ser:
                stx = b'\x02'
                addr = b'01'
                etx = b'\x03'

                cmd_type = mode.encode('ascii')
                cmd = command.encode('ascii')
                value_bytes = value.encode('ascii') if value else b''
                
                raw_message = stx + addr + cmd_type + cmd + value_bytes + etx
                bcc = self._calculate_bcc(raw_message)
                request_message = raw_message + bcc
                
                ser.flushInput()
                ser.rts = True
                time.sleep(0.01)
                ser.write(request_message)
                ser.rts = False
                

                time.sleep(0.1)
                response = ser.read(256)

        except serial.SerialException as e:
            raise DeviceConnectionException(f"Serial communication error: {e}")

        if len(response) == 0:
            raise DeviceNoResponseException()

        if len(response) < 6:
            raise DeviceUnknownException(f"Incomplete response: {response}")

        if response[3] == 0x15:
            error_code = response[7:-2].decode(errors='ignore')
            raise DeviceNAKException(error_code)

        if response[3] != 0x06:
            raise UnexpectedResponseException(command, response.decode(errors='ignore'))

        return response[7:-2].decode(errors='ignore').strip()

    def read_temperature(self):
        value = self._send_command("PV1")
        return int(value) / 10.0

    def read_output_status(self):
        return self._send_command("OM1")

    def read_error_status1(self):
        return self._send_command("ER1")

    def read_error_status2(self):
        return self._send_command("ER2")

    def read_bcc_setting(self):
        return self._send_command("BCC")

    def read_operation_step(self):
        return self._send_command(" TI")

    def set_temperature(self, value: float):
        self._send_command("SV1", f"{int(value * 10):05d}")

    def set_timer(self, minutes: int):
        self._send_command("TIM", f"{minutes:05d}")

    def set_timer_mode(self, mode: TimerMode):
        self._send_command("TMS", mode.value)

    def start(self):
        self._send_command("RUN", "00001")

    def stop(self):
        self._send_command("RUN", "00000")

    def lock(self):
        self._send_command("LOC", "00001")

    def unlock(self):
        self._send_command("LOC", "00000")

    def set_operation_type(self, mode: OperationType):
        self._send_command("RST", mode.value)

    def set_baudrate(self, value: int):
        self._send_command("BPS", f"{value:05d}")

    def set_stopbits(self, value: int):
        self._send_command("SPB", str(value))

    def set_data_bits(self, value: int):
        self._send_command("DAT", str(value))

    def set_parity(self, mode: ParityMode):
        self._send_command("PAL", mode.value)

    def set_address(self, value: int):
        self._send_command("ADR", f"{value:02d}")

    def set_response_delay(self, value: int):
        self._send_command("AWT", f"{value:05d}")

    def set_mode(self, mode: AccessMode):
        self._send_command("MOD", mode.value)

    @staticmethod
    def cast_argument(arg):
        try:
            if "." in arg:
                return float(arg)
            return int(arg)
        except ValueError:
            pass

        enums = {
            'TimerMode': TimerMode,
            'OperationType': OperationType,
            'ParityMode': ParityMode,
            'AccessMode': AccessMode,
        }

        for enum_type in enums.values():
            try:
                return enum_type[arg]
            except KeyError:
                continue

        return arg

    @staticmethod
    def help():
        return """Available Commands:
  read_temperature()                            -> float      Get current temperature.
  read_output_status()                          -> str        Get output status.
  read_error_status1()                          -> str        Get error status 1.
  read_error_status2()                          -> str        Get error status 2.
  set_temperature(value: float)                 -> None       Set temperature (e.g., 25.5).
  set_timer(minutes: int)                       -> None       Set timer in minutes.
  set_timer_mode(mode: TimerMode)               -> None       Set timer mode. (AUTO_STOP, AUTO_START)
  start()                                       -> None       Start operation.
  stop()                                        -> None       Stop operation.
  lock()                                        -> None       Lock controls.
  unlock()                                      -> None       Unlock controls.
  set_operation_type(mode: OperationType)       -> None       Set operation type. (FIXED, TIMER)
  set_baudrate(value: int)                      -> None       Set baudrate.
  set_stopbits(value: int)                      -> None       Set stop bits.
  set_data_bits(value: int)                     -> None       Set data bits.
  set_parity(mode: ParityMode)                  -> None       Set parity mode. (NONE, ODD, EVEN)
  set_address(value: int)                       -> None       Set device address.
  set_response_delay(value: int)                -> None       Set response delay.
  set_mode(mode: AccessMode)                    -> None       Set access mode. (ON, OFF)
"""



