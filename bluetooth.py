from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from bleak import BleakClient
from bleak.exc import BleakError
from datetime import timedelta
import asyncio

from .const import (
    DOMAIN,
    NOTIFY_CHARACTERISTIC_UUID,
    WRITE_CHARACTERISTIC_UUID,
    CONTROLLER_REALDATA_CMD,
    CONTROLLER_REALDATA_WITH_CRC_CMD,
    NEW_DEVICE_REALDATA_CMD,
    OLD_DEVICE_REALDATA_CMD,
    MIN_DATA_LENGTH,
    RESPONSE_TIMEOUT,
    CONNECTION_TIMEOUT,
    UPDATE_INTERVAL,
)
import logging
import struct

_LOGGER = logging.getLogger(__name__)

def parse_mppt_packet(data: bytes) -> dict:
    """Parse MPPT data packet from Bluetooth notification."""
    if len(data) < MIN_DATA_LENGTH:
        raise ValueError(f"Data too short: expected at least {MIN_DATA_LENGTH} bytes, got {len(data)}")
    
    # Parse MPPT data using confirmed byte offsets
    # Basic readings
    battery_volt_raw = int.from_bytes(data[5:7], "big")
    battery_current_raw = int.from_bytes(data[7:9], "big")
    battery_temp_raw = data[10]
    
    solar_volt_raw = int.from_bytes(data[17:19], "big")
    solar_current_raw = int.from_bytes(data[19:21], "big")
    solar_power_raw = int.from_bytes(data[21:23], "big")
    
    # Additional data fields from JavaScript analysis
    # Load/output data (if available)
    load_volt_raw = int.from_bytes(data[11:13], "big") if len(data) > 13 else 0
    load_current_raw = int.from_bytes(data[13:15], "big") if len(data) > 15 else 0
    load_power_raw = int.from_bytes(data[15:17], "big") if len(data) > 17 else 0
    
    # Status and state information
    charging_state = data[23] if len(data) > 23 else 0
    error_code = data[24] if len(data) > 24 else 0
    
    # Daily statistics (if available in longer packets)
    daily_energy_raw = int.from_bytes(data[25:27], "big") if len(data) > 27 else 0
    total_energy_raw = int.from_bytes(data[27:31], "big") if len(data) > 31 else 0
    
    # Maximum values recorded
    max_solar_volt_raw = int.from_bytes(data[31:33], "big") if len(data) > 33 else solar_volt_raw
    max_battery_volt_raw = int.from_bytes(data[33:35], "big") if len(data) > 35 else battery_volt_raw
    max_charging_current_raw = int.from_bytes(data[35:37], "big") if len(data) > 37 else battery_current_raw

    parsed_data = {
        # Basic MPPT readings
        "solar_voltage": round(solar_volt_raw * 0.1, 2),
        "solar_current": round(solar_current_raw * 0.01, 2),
        "solar_power": solar_power_raw,
        "battery_voltage": round(battery_volt_raw * 0.1, 2),
        "battery_current": round(battery_current_raw * 0.01, 2),
        "battery_temperature": round(float(battery_temp_raw), 1),
        
        # Load/output readings
        "load_voltage": round(load_volt_raw * 0.1, 2),
        "load_current": round(load_current_raw * 0.01, 2),
        "load_power": load_power_raw,
        
        # Status information
        "charging_state": charging_state,
        "error_code": error_code,
        
        # Energy statistics
        "daily_energy": round(daily_energy_raw * 0.01, 2),  # kWh
        "total_energy": round(total_energy_raw * 0.01, 2),  # kWh
        
        # Maximum values
        "max_solar_voltage": round(max_solar_volt_raw * 0.1, 2),
        "max_battery_voltage": round(max_battery_volt_raw * 0.1, 2),
        "max_charging_current": round(max_charging_current_raw * 0.01, 2),
    }
    
    # Enhanced logging with additional data
    _LOGGER.info("MPPT Data - HEX: %s | Solar: %.1fV/%.2fA/%dW | Battery: %.1fV/%.2fA/%.1f°C | Load: %.1fV/%.2fA/%dW | State: %d | Daily: %.2fkWh", 
                data.hex(), 
                parsed_data["solar_voltage"], parsed_data["solar_current"], parsed_data["solar_power"],
                parsed_data["battery_voltage"], parsed_data["battery_current"], parsed_data["battery_temperature"],
                parsed_data["load_voltage"], parsed_data["load_current"], parsed_data["load_power"],
                parsed_data["charging_state"], parsed_data["daily_energy"])
    
    return parsed_data

class MPPTBLECoordinator(DataUpdateCoordinator):
    """Coordinator for MPPT BLE device using notifications."""

    def __init__(self, hass: HomeAssistant, entry):
        """Initialize the coordinator."""
        self._mac_address = entry.data["mac_address"].upper()
        self._entry = entry
        self._client = None
        self._latest_data = None
        self._notification_received = asyncio.Event()
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        
        _LOGGER.info("Initializing MPPT BLE Coordinator for MAC address: %s", self._mac_address)
        _LOGGER.info("Using notification-based approach")

    def _get_bluetooth_diagnostics(self) -> dict:
        """Get Bluetooth diagnostic information."""
        diagnostics = {
            "link_quality": None,
            "signal_strength": None,
        }
        
        try:
            # Get RSSI from Home Assistant's Bluetooth integration
            from homeassistant.components.bluetooth import async_last_service_info
            
            service_info = async_last_service_info(self.hass, self._mac_address, connectable=True)
            
            if service_info and hasattr(service_info, 'advertisement') and service_info.advertisement.rssi is not None:
                # RSSI (Received Signal Strength Indicator) in dBm
                rssi = service_info.advertisement.rssi
                diagnostics["signal_strength"] = rssi
                
                # Calculate link quality percentage based on RSSI
                # RSSI typically ranges from -30 (excellent) to -90 (poor)
                # Convert to 0-100% scale
                if rssi >= -30:
                    link_quality = 100
                elif rssi <= -90:
                    link_quality = 0
                else:
                    # Linear interpolation between -30 and -90 dBm
                    link_quality = int(((rssi + 90) / 60) * 100)
                
                diagnostics["link_quality"] = max(0, min(100, link_quality))
                
        except Exception as e:
            _LOGGER.debug("Could not get Bluetooth diagnostics: %s", e)
            
        return diagnostics

    async def _send_data_request_command(self):
        """Send command to request fresh MPPT data."""
        try:
            if not self._client or not self._client.is_connected:
                return False
                
            # Find the write characteristic
            write_char_uuid = WRITE_CHARACTERISTIC_UUID
            write_char = None
            for service in self._client.services:
                for char in service.characteristics:
                    if char.uuid.lower() == write_char_uuid.lower():
                        write_char = char
                        break
                if write_char:
                    break
            
            if write_char:
                # Send the main real-time data command
                cmd = bytes.fromhex(CONTROLLER_REALDATA_CMD)
                await self._client.write_gatt_char(write_char, cmd)
                self._notification_received.clear()
                return True
            else:
                _LOGGER.debug("Write characteristic not found for periodic command")
                return False
                
        except Exception as e:
            _LOGGER.debug("Failed to send periodic data request command: %s", e)
            return False

    def notification_handler(self, sender, data):
        """Handle incoming notifications from the MPPT device."""
        try:
            # Check if this is a short notification (device acknowledgment)
            if len(data) < 23:
                _LOGGER.debug("Received short notification (%d bytes) - likely device acknowledgment", len(data))
                self._notification_received.set()
                return
            
            # Parse the full MPPT data notification (logging happens inside parse_mppt_packet)
            parsed_data = parse_mppt_packet(data)
            
            # Add Bluetooth diagnostic information
            parsed_data.update(self._get_bluetooth_diagnostics())
            
            self._latest_data = parsed_data
            self._notification_received.set()
            
            # Update the coordinator data immediately
            self.async_set_updated_data(parsed_data)
            
        except Exception as e:
            _LOGGER.error("Error parsing notification data: %s", e)
            self._notification_received.set()

    async def _async_update_data(self):
        """Connect to device and set up notifications."""
        _LOGGER.debug("Starting notification setup for device %s", self._mac_address)
        
        try:
            # Get the Bluetooth device
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._mac_address, connectable=True
            )
            
            if not ble_device:
                _LOGGER.warning("BLE device %s not found or not connectable", self._mac_address)
                raise UpdateFailed(f"Device {self._mac_address} not found")

            # If we already have a client connected, check if it's still connected
            if self._client and self._client.is_connected:
                _LOGGER.debug("Client already connected, sending periodic command for fresh data")
                # Send periodic command to trigger fresh data
                await self._send_data_request_command()
                # Wait for response
                try:
                    await asyncio.wait_for(self._notification_received.wait(), timeout=5.0)
                    return self._latest_data
                except asyncio.TimeoutError:
                    _LOGGER.warning("No notifications received after command, will reconnect")

            _LOGGER.debug("Connecting to device %s", self._mac_address)
            
            # Create new client with better timeout handling
            try:
                # Clean up any existing client first
                if self._client:
                    try:
                        await self._client.disconnect()
                    except:
                        pass
                    self._client = None
                
                self._client = BleakClient(ble_device, timeout=CONNECTION_TIMEOUT)
                _LOGGER.debug("Created BleakClient, attempting connection...")
                
                # Use asyncio.wait_for for better timeout control
                await asyncio.wait_for(self._client.connect(), timeout=CONNECTION_TIMEOUT)
                _LOGGER.info("Connected to MPPT device %s", self._mac_address)
                
            except asyncio.TimeoutError:
                _LOGGER.warning("Connection timeout to device %s - will retry later", self._mac_address)
                self._client = None
                # Don't raise error, just return None to keep trying
                return None
            except asyncio.CancelledError:
                _LOGGER.debug("Connection cancelled to device %s", self._mac_address)
                self._client = None
                return None
            except Exception as e:
                _LOGGER.warning("Failed to connect to device %s: %s", self._mac_address, e)
                self._client = None
                # Don't raise error, just return None to keep trying
                return None
            
            # Discover services
            services = self._client.services
            service_count = len(list(services))
            _LOGGER.info("Discovered %d services", service_count)
            
            # Log all services for debugging
            for service in services:
                _LOGGER.debug("Service: %s", service.uuid)
                for char in service.characteristics:
                    _LOGGER.debug("  Characteristic: %s - Properties: %s", 
                                char.uuid, char.properties)
            
            # Find the notification characteristic from constants
            target_char_uuid = NOTIFY_CHARACTERISTIC_UUID
            
            # Find the notification characteristic
            target_char = None
            for service in services:
                for char in service.characteristics:
                    if char.uuid.lower() == target_char_uuid.lower():
                        target_char = char
                        break
                if target_char:
                    break
            
            if not target_char:
                _LOGGER.error("Could not find notification characteristic %s", target_char_uuid)
                raise UpdateFailed("Notification characteristic not found")
            
            _LOGGER.info("Found notification characteristic: %s", target_char.uuid)
            
            # Start notifications
            await self._client.start_notify(target_char, self.notification_handler)
            _LOGGER.info("Started notifications on characteristic %s", target_char.uuid)
            
            # Since the Android app gets data, we need to find the right trigger
            # Let's try writing to the write characteristic with different commands
            write_char_uuid = WRITE_CHARACTERISTIC_UUID
            write_char = None
            for service in services:
                for char in service.characteristics:
                    if char.uuid.lower() == write_char_uuid.lower():
                        write_char = char
                        break
                if write_char:
                    break
            
            if write_char:
                _LOGGER.info("Found write characteristic, trying different trigger commands...")
                
                # Use the exact commands from the Android app JavaScript
                trigger_commands = [
                    bytes.fromhex(CONTROLLER_REALDATA_CMD),  # ControllerRealdata - main real-time data command
                    bytes.fromhex(CONTROLLER_REALDATA_WITH_CRC_CMD),  # Complete command with CRC
                    bytes.fromhex(NEW_DEVICE_REALDATA_CMD),  # NewDeviceRealdata
                    bytes.fromhex(OLD_DEVICE_REALDATA_CMD),  # OldDeviceRealdata
                ]
                
                for i, cmd in enumerate(trigger_commands):
                    try:
                        _LOGGER.info("Sending trigger command %d: %s", i+1, cmd.hex())
                        await self._client.write_gatt_char(write_char, cmd)
                        
                        # Wait for response after each command
                        self._notification_received.clear()
                        try:
                            await asyncio.wait_for(self._notification_received.wait(), timeout=RESPONSE_TIMEOUT)
                            if self._latest_data:
                                _LOGGER.info("SUCCESS! Command %d triggered MPPT data", i+1)
                                return self._latest_data
                        except asyncio.TimeoutError:
                            _LOGGER.debug("Command %d: no response", i+1)
                            continue
                            
                    except Exception as e:
                        _LOGGER.debug("Failed to send command %d: %s", i+1, e)
                        continue
            
            # Try reading from all readable characteristics to see if any contain data
            _LOGGER.info("Trying to read from all readable characteristics...")
            for service in services:
                for char in service.characteristics:
                    if "read" in char.properties:
                        try:
                            data = await self._client.read_gatt_char(char)
                            _LOGGER.info("Read %d bytes from %s: %s", len(data), char.uuid, data.hex())
                            
                            if len(data) >= 23:
                                try:
                                    parsed_data = parse_mppt_packet(data)
                                    _LOGGER.info("SUCCESS! Found MPPT data in characteristic %s: %s", char.uuid, parsed_data)
                                    self._latest_data = parsed_data
                                    self.async_set_updated_data(parsed_data)
                                    return parsed_data
                                except Exception as e:
                                    _LOGGER.debug("Data from %s not MPPT format: %s", char.uuid, e)
                                    
                        except Exception as e:
                            _LOGGER.debug("Failed to read from %s: %s", char.uuid, e)
            
            # Final attempt - wait longer for any notifications
            _LOGGER.info("Final attempt - waiting 30 seconds for any notifications...")
            self._notification_received.clear()
            try:
                await asyncio.wait_for(self._notification_received.wait(), timeout=30.0)
                if self._latest_data:
                    _LOGGER.info("Received MPPT data via notifications")
                    return self._latest_data
            except asyncio.TimeoutError:
                pass
            
            _LOGGER.warning("Could not retrieve MPPT data despite device being active")
            _LOGGER.info("Connection will stay alive for future attempts")
            return None
                
        except BleakError as e:
            _LOGGER.error("Bluetooth connection error: %s", e)
            if self._client:
                try:
                    await self._client.disconnect()
                except:
                    pass
                self._client = None
            raise UpdateFailed(f"Connection failed: {e}")
        except Exception as e:
            _LOGGER.error("Unexpected error: %s", e, exc_info=True)
            if self._client:
                try:
                    await self._client.disconnect()
                except:
                    pass
                self._client = None
            raise UpdateFailed(f"Update failed: {e}")

    async def async_shutdown(self):
        """Shutdown the coordinator and disconnect."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
                _LOGGER.info("Disconnected from MPPT device")
            except Exception as e:
                _LOGGER.debug("Error during shutdown disconnect: %s", e)
            finally:
                self._client = None
