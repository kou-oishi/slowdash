import pymysql
from db_config import DB_CONFIG


class ChillerMySQL:
    _command_map = None  # Class variable for command name -> ID mapping

    def __init__(self):
        # Connect to database
        self.connection = pymysql.connect(**DB_CONFIG, autocommit=True)

        # Load command map only once across all instances
        if ChillerMySQL._command_map is None:
            ChillerMySQL._load_command_map(self)

    @classmethod
    def _load_command_map(cls, instance):
        # Load command_id and name mapping from chiller_command
        command_map = {}
        with instance.connection.cursor() as cursor:
            cursor.execute("SELECT id, name FROM chiller_command")
            for command_id, name in cursor.fetchall():
                command_map[name] = command_id

        if not command_map:
            raise RuntimeError("chiller_command table is empty or not available.")

        cls._command_map = command_map

    def _format_channel(self, channel):
        # Convert channel number (int) to ".Chiller.xx" format
        return f"Chiller.{channel:02d}"

    def ensure_channel_registered(self, channel):
        # Insert new channel into chiller_channel if not exists
        channel_str = self._format_channel(channel)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM chiller_channel WHERE channel = %s", (channel_str,))
            if cursor.fetchone() is None:
                cursor.execute(
                    "INSERT INTO chiller_channel (channel, detail) VALUES (%s, %s)",
                    (channel_str, "Auto-registered upon server start.")
                )

    def insert_temperature(self, channel, temperature):
        # Insert temperature measurement record
        channel_str = self._format_channel(channel)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO chiller_temperature (timestamp, channel, temperature)
                VALUES (NOW(), %s, %s)
                """,
                (channel_str, temperature)
            )

    def insert_error(self, channel, error_type, detail):
        # ERROR LOGGING
        channel_str = self._format_channel(channel)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO chiller_error (timestamp, channel, type, detail)
                VALUES (NOW(), %s, %s, %s)
                """,
                (channel_str, error_type, detail)
            )

    def insert_history(self, channel, command_name, args, response):
        # Insert command execution history record
        if command_name not in self._command_map:
            raise ValueError(f"Command '{command_name}' not found in chiller_command table.")

        command_id = self._command_map[command_name]
        channel_str = self._format_channel(channel)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO chiller_history (timestamp, channel, command_id, args, response)
                VALUES (NOW(), %s, %s, %s, %s)
                """,
                (channel_str, command_id, args, response)
            )

    def close(self):
        # Close the connection
        self.connection.close()

