from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, BluetoothChange
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, SERVICE_UUID
import logging
import struct

_LOGGER = logging.getLogger(__name__)

def parse_mppt_packet(data: bytes) -> dict:
    # Ensure the data length is sufficient for all expected parameters
    # The longest offset is 21, so we need at least 22 bytes (offset 21 + 2 bytes for uint16)
    if len(data) < 23:
        raise ValueError(f"Data too short: expected at least 23 bytes, got {len(data)}")
    
    # Confirmed offsets and multipliers:
    # Solar Panels Voltage: Multiplier: 0.1, Byte Offset: 17
    # Solar Panels Current: Multiplier: 0.01, Byte Offset: 19
    # Solar Panels Power: Multiplier: 1, Byte Offset: 21
    # Battery Voltage: Multiplier: 0.1, Byte Offset: 5
    # Battery Current: Multiplier: 0.01, Byte Offset: 7
    # Battery Temperature: Multiplier: 0.1, Byte Offset: 9

    battery_volt_raw = int.from_bytes(data[5:7], "little")
    battery_current_raw = int.from_bytes(data[7:9], "little")
    battery_temp_raw = int.from_bytes(data[9:11], "little", signed=True) # Temperature can be negative
    solar_volt_raw = int.from_bytes(data[17:19], "little")
    solar_current_raw = int.from_bytes(data[19:21], "little")
    solar_power_raw = int.from_bytes(data[21:23], "little")

    return {
        "solar_voltage": round(solar_volt_raw * 0.1, 2),
        "solar_current": round(solar_current_raw * 0.01, 2),
        "solar_power": solar_power_raw,
        "battery_voltage": round(battery_volt_raw * 0.1, 2),
        "battery_current": round(battery_current_raw * 0.01, 2),
        "battery_temperature": round(battery_temp_raw * 0.1, 2),
    }

class MPPTBLECoordinator(PassiveBluetoothDataUpdateCoordinator):
    def __init__(self, hass, entry):
        self._mac_address = entry.data["mac_address"].upper()
        self._entry = entry
        _LOGGER.info("Initializing MPPT BLE Coordinator for MAC address: %s", self._mac_address)
        super().__init__(
            hass, 
            _LOGGER, 
            address=self._mac_address, 
            mode=BluetoothChange.ADVERTISEMENT
        )
        self.data = {}
        _LOGGER.info("MPPT BLE Coordinator initialized successfully")


    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: str
    ) -> None:
        """Handle Bluetooth event."""
        _LOGGER.debug("Received Bluetooth event from %s (change: %s)", service_info.address, change)
        
        # Only process events from our specific MAC address
        if service_info.address.upper() != self._mac_address:
            _LOGGER.debug("Ignoring event from %s (not our target %s)", service_info.address, self._mac_address)
            return
            
        _LOGGER.info("Processing Bluetooth event from target device %s", service_info.address)
        _LOGGER.debug("Service info: name=%s, rssi=%s", service_info.name, service_info.rssi)
        
        try:
            if service_info.manufacturer_data:
                _LOGGER.debug("Found manufacturer data: %s", service_info.manufacturer_data)
                for manufacturer_id, value in service_info.manufacturer_data.items():
                    _LOGGER.debug("Processing manufacturer data from ID %s: %s bytes", manufacturer_id, len(value))
                    _LOGGER.debug("Raw data: %s", value.hex())
                    decoded = parse_mppt_packet(value)
                    _LOGGER.info("Successfully decoded MPPT data from %s: %s", service_info.address, decoded)
                    self.data = decoded
                    self.async_set_updated_data(decoded)
                    break
            else:
                _LOGGER.warning("No manufacturer data found in Bluetooth advertisement from %s", service_info.address)
                
        except ValueError as e:
            _LOGGER.warning("Data parsing error from %s: %s", service_info.address, e)
        except Exception as e:
            _LOGGER.error("Unexpected error parsing MPPT BLE data from %s: %s", service_info.address, e, exc_info=True)
