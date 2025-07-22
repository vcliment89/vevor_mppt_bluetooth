from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from bleak import BleakClient
from bleak.exc import BleakError
from datetime import timedelta
import asyncio

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

class MPPTBLECoordinator(DataUpdateCoordinator):
    """Coordinator for MPPT BLE device using active connection."""

    def __init__(self, hass: HomeAssistant, entry):
        """Initialize the coordinator."""
        self._mac_address = entry.data["mac_address"].upper()
        self._entry = entry
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Update every 30 seconds
        )
        
        _LOGGER.info("Initializing MPPT BLE Coordinator for MAC address: %s", self._mac_address)
        _LOGGER.info("Using active Bluetooth connection approach")

    async def _async_update_data(self):
        """Fetch data from the MPPT device."""
        _LOGGER.debug("Starting data update for device %s", self._mac_address)
        
        try:
            # Get the Bluetooth device
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._mac_address, connectable=True
            )
            
            if not ble_device:
                _LOGGER.warning("BLE device %s not found or not connectable", self._mac_address)
                raise UpdateFailed(f"Device {self._mac_address} not found")

            _LOGGER.debug("Connecting to device %s", self._mac_address)
            
            # Try connection with timeout and better error handling
            try:
                client = BleakClient(ble_device, timeout=10.0)
                await client.connect()
                _LOGGER.info("Successfully connected to MPPT device %s", self._mac_address)
                
                try:
                    # Check if we're actually connected
                    if not client.is_connected:
                        _LOGGER.error("Client reports not connected after connection attempt")
                        raise UpdateFailed("Connection failed - client not connected")
                    
                    # List all services and characteristics for debugging
                    _LOGGER.debug("Discovering services...")
                    services = client.services
                    
                    if not services:
                        _LOGGER.warning("No services discovered on device")
                        raise UpdateFailed("No services found on device")
                    
                    _LOGGER.info("Discovered %d services on device", len(services))
                    
                    for service in services:
                        _LOGGER.debug("Service: %s (%s)", service.uuid, service.description)
                        for char in service.characteristics:
                            _LOGGER.debug("  Characteristic: %s - Properties: %s", 
                                        char.uuid, char.properties)
                    
                    # Try to find the service UUID from const.py
                    service_uuid = SERVICE_UUID
                    _LOGGER.debug("Looking for service UUID: %s", service_uuid)
                    
                    # First, try to find a characteristic that can be read
                    target_service = None
                    for service in services:
                        if service.uuid.lower() == service_uuid.lower():
                            target_service = service
                            break
                    
                    if target_service:
                        _LOGGER.info("Found target service: %s", target_service.uuid)
                        
                        # Look for readable characteristics
                        for char in target_service.characteristics:
                            if "read" in char.properties:
                                _LOGGER.debug("Trying to read from characteristic: %s", char.uuid)
                                try:
                                    data = await client.read_gatt_char(char.uuid)
                                    _LOGGER.info("Read %d bytes from characteristic %s", len(data), char.uuid)
                                    _LOGGER.debug("Raw data: %s", data.hex())
                                    
                                    # Try to parse the data
                                    parsed_data = parse_mppt_packet(data)
                                    _LOGGER.info("Successfully parsed MPPT data: %s", parsed_data)
                                    return parsed_data
                                    
                                except Exception as e:
                                    _LOGGER.debug("Failed to read from characteristic %s: %s", char.uuid, e)
                                    continue
                    else:
                        _LOGGER.warning("Target service %s not found", service_uuid)
                        
                        # Try to read from any readable characteristic as fallback
                        _LOGGER.info("Trying to read from any available characteristic...")
                        for service in services:
                            for char in service.characteristics:
                                if "read" in char.properties:
                                    try:
                                        _LOGGER.debug("Trying characteristic %s in service %s", char.uuid, service.uuid)
                                        data = await client.read_gatt_char(char.uuid)
                                        if len(data) >= 23:  # Our parser needs at least 23 bytes
                                            _LOGGER.info("Found potential data in %s: %d bytes", char.uuid, len(data))
                                            _LOGGER.debug("Raw data: %s", data.hex())
                                            parsed_data = parse_mppt_packet(data)
                                            _LOGGER.info("Successfully parsed MPPT data: %s", parsed_data)
                                            return parsed_data
                                    except Exception as e:
                                        _LOGGER.debug("Failed to read from %s: %s", char.uuid, e)
                                        continue
                    
                    _LOGGER.warning("No readable data found on device")
                    raise UpdateFailed("No MPPT data available")
                    
                finally:
                    # Always disconnect
                    try:
                        await client.disconnect()
                        _LOGGER.debug("Disconnected from device")
                    except Exception as e:
                        _LOGGER.debug("Error during disconnect: %s", e)
                        
            except asyncio.TimeoutError:
                _LOGGER.error("Connection timeout to device %s", self._mac_address)
                raise UpdateFailed("Connection timeout")
            except BleakError as e:
                _LOGGER.error("Bluetooth connection error: %s", e)
                raise UpdateFailed(f"Connection failed: {e}")
                
        except UpdateFailed:
            raise  # Re-raise UpdateFailed exceptions
        except Exception as e:
            _LOGGER.error("Unexpected error: %s", e, exc_info=True)
            raise UpdateFailed(f"Update failed: {e}")
