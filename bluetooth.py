from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, BluetoothChange
from homeassistant.components import bluetooth
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
        
        # Use the MAC address but make event handler more permissive for debugging
        super().__init__(
            hass, 
            _LOGGER, 
            address=self._mac_address, 
            mode=BluetoothChange.ADVERTISEMENT
        )
        self.data = None  # Initialize as None instead of empty dict
        _LOGGER.info("MPPT BLE Coordinator initialized successfully")
        
        # Add a test to see if Bluetooth is working at all
        _LOGGER.info("Testing Bluetooth integration availability...")
        try:
            from homeassistant.components import bluetooth
            if hasattr(bluetooth, 'async_get_scanner'):
                _LOGGER.info("Bluetooth scanner available")
                
                # Try to get current Bluetooth devices
                try:
                    scanner = bluetooth.async_get_scanner(hass)
                    if scanner:
                        _LOGGER.info("Bluetooth scanner instance obtained")
                        # Get discovered devices
                        discovered = bluetooth.async_discovered_service_info(hass)
                        _LOGGER.info("Found %d discovered Bluetooth devices", len(discovered))
                        
                        # Log some device info for debugging
                        device_list = list(discovered)[:5]  # Convert to list and get first 5
                        for i, device_info in enumerate(device_list):
                            _LOGGER.info("Device %d: %s (%s) - name='%s'", 
                                       i+1, device_info.address, device_info.rssi, device_info.name)
                            if device_info.address.upper() == self._mac_address:
                                _LOGGER.warning("Found our target device in discovered devices!")
                        
                        # Also check if our target device is in the full list
                        target_found = False
                        for device_info in discovered:
                            if device_info.address.upper() == self._mac_address:
                                _LOGGER.warning("TARGET DEVICE FOUND: %s - name='%s', rssi=%s", 
                                              device_info.address, device_info.name, device_info.rssi)
                                target_found = True
                                break
                        
                        if not target_found:
                            _LOGGER.warning("Target device %s NOT found in %d discovered devices", 
                                          self._mac_address, len(discovered))
                        else:
                            # Device found! Let's examine its data
                            for device_info in discovered:
                                if device_info.address.upper() == self._mac_address:
                                    _LOGGER.info("EXAMINING TARGET DEVICE DATA:")
                                    _LOGGER.info("  Address: %s", device_info.address)
                                    _LOGGER.info("  Name: %s", device_info.name)
                                    _LOGGER.info("  RSSI: %s", device_info.rssi)
                                    _LOGGER.info("  Manufacturer data: %s", device_info.manufacturer_data)
                                    _LOGGER.info("  Service data: %s", device_info.service_data)
                                    _LOGGER.info("  Service UUIDs: %s", device_info.service_uuids)
                                    break
                    else:
                        _LOGGER.warning("Could not get Bluetooth scanner instance")
                except Exception as e:
                    _LOGGER.warning("Error checking discovered devices: %s", e)
            else:
                _LOGGER.warning("Bluetooth scanner not available")
        except ImportError:
            _LOGGER.error("Bluetooth integration not available")


    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: str
    ) -> None:
        """Handle Bluetooth event."""
        # Log ALL Bluetooth events for debugging
        _LOGGER.info("Bluetooth event: %s (%s) - name='%s', rssi=%s", 
                    service_info.address, change, service_info.name, service_info.rssi)
        
        # Check if this might be our device by name
        if service_info.name and "BT-TH" in service_info.name:
            _LOGGER.warning("Found device with BT-TH name: %s (%s) - is this your MPPT device?", 
                           service_info.name, service_info.address)
        
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
                _LOGGER.debug("Available data - service_data: %s, service_uuids: %s", 
                             service_info.service_data, service_info.service_uuids)
                
        except ValueError as e:
            _LOGGER.warning("Data parsing error from %s: %s", service_info.address, e)
        except Exception as e:
            _LOGGER.error("Unexpected error parsing MPPT BLE data from %s: %s", service_info.address, e, exc_info=True)
