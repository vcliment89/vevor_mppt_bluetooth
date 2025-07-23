"""Constants for the VEVOR MPPT Bluetooth integration."""

DOMAIN = "vevor_mppt_bluetooth"

# Configuration keys
CONF_NAME = "name"
CONF_MAC_ADDRESS = "mac_address"

# Default values
DEFAULT_NAME = "VEVOR MPPT"

# Bluetooth UUIDs (from Android app reverse engineering)
NOTIFY_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_SERVICE_UUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_CHARACTERISTIC_UUID = "0000ffd1-0000-1000-8000-00805f9b34fb"

# MPPT Commands (from Android app JavaScript)
CONTROLLER_REALDATA_CMD = "FF0301000023"
CONTROLLER_REALDATA_WITH_CRC_CMD = "FF03010000231031"
NEW_DEVICE_REALDATA_CMD = "FF0300FD000D"
OLD_DEVICE_REALDATA_CMD = "FF030100000A"

# Data parsing constants
MIN_DATA_LENGTH = 46  # Minimum bytes needed for full MPPT data
RESPONSE_TIMEOUT = 5.0  # Seconds to wait for command response
CONNECTION_TIMEOUT = 10.0  # Seconds to wait for Bluetooth connection
UPDATE_INTERVAL = 60  # Seconds between coordinator updates

# Device identification
DEVICE_NAME_PREFIX = "BT-TH"

# Legacy constant for backward compatibility
SERVICE_UUID = NOTIFY_CHARACTERISTIC_UUID
