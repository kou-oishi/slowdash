CREATE TABLE chiller_temperature (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    channel VARCHAR(16) NOT NULL,
    temperature FLOAT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE chiller_channel (
    channel VARCHAR(16) NOT NULL,
    detail TEXT,
    PRIMARY KEY (channel)
);

CREATE TABLE chiller_error (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    channel VARCHAR(16) NOT NULL,
    type VARCHAR(255) NOT NULL,
    detail TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE chiller_command (
    id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL UNIQUE,
    description TEXT,
    PRIMARY KEY (id)
);

INSERT IGNORE INTO chiller_command (id, name, description) VALUES
(1, 'read_temperature', 'Get current temperature.'),
(2, 'read_output_status', 'Get output status.'),
(3, 'read_error_status1', 'Get error status 1.'),
(4, 'read_error_status2', 'Get error status 2.'),
(5, 'set_temperature', 'Set temperature (float).'),
(6, 'set_timer', 'Set timer in minutes (int).'),
(7, 'set_timer_mode', 'Set timer mode (AUTO_STOP, AUTO_START).'),
(8, 'start', 'Start operation.'),
(9, 'stop', 'Stop operation.'),
(10, 'lock', 'Lock controls.'),
(11, 'unlock', 'Unlock controls.'),
(12, 'set_operation_type', 'Set operation type (FIXED, TIMER).'),
(13, 'set_baudrate', 'Set baudrate (int).'),
(14, 'set_stopbits', 'Set stop bits (int).'),
(15, 'set_data_bits', 'Set data bits (int).'),
(16, 'set_parity', 'Set parity mode (NONE, ODD, EVEN).'),
(17, 'set_address', 'Set device address (int).'),
(18, 'set_response_delay', 'Set response delay (int).'),
(19, 'set_mode', 'Set access mode (ON, OFF).'),
(50, 'pause_monitor', 'Pause temperature monitoring for a specific channel.'),
(51, 'resume_monitor', 'Resume temperature monitoring for a specific channel.');

CREATE TABLE chiller_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    channel VARCHAR(16) NOT NULL,
    command_id TINYINT UNSIGNED NOT NULL,
    args VARCHAR(255),
    response VARCHAR(255),
    PRIMARY KEY (id),
    FOREIGN KEY (command_id) REFERENCES chiller_command(id)
);

