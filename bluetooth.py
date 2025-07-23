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
    # The nRF log shows 70-byte packets, let's be more flexible with length
    if len(data) < 23:
        raise ValueError(f"Data too short: expected at least 23 bytes, got {len(data)}")
    
    # Based on nRF log analysis, the data format appears different
    # Let's try to parse the actual format from the log
    # Example from log: FF-03-46-00-64-00-8F-00-80-26-19-00...
    
    try:
        # Try original parsing first
        battery_volt_raw = int.from_bytes(data[5:7], "little")
        battery_current_raw = int.from_bytes(data[7:9], "little")
        battery_temp_raw = int.from_bytes(data[9:11], "little", signed=True)
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
    except Exception as e:
        # If original parsing fails, try alternative parsing based on nRF log
        _LOGGER.debug("Original parsing failed, trying alternative: %s", e)
        
        # From nRF log, let's try different offsets
        # The packets start with FF-03, so real data might start at offset 2
        if len(data) >= 30:
            try:
                # Try parsing with different offsets based on the nRF log pattern
                alt_battery_volt = int.from_bytes(data[7:9], "little")
                alt_solar_volt = int.from_bytes(data[9:11], "little") 
                
                return {
                    "solar_voltage": round(alt_solar_volt * 0.1, 2),
                    "solar_current": 0.0,  # Will need to find correct offset
                    "solar_power": 0,
                    "battery_voltage": round(alt_battery_volt * 0.1, 2),
                    "battery_current": 0.0,
                    "battery_temperature": 25.0,  # Default value
                }
            except Exception:
                pass
        
        raise ValueError(f"Could not parse MPPT data from {len(data)} bytes")

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
            update_interval=timedelta(seconds=60),  # Check connection every 60 seconds
        )
        
        _LOGGER.info("Initializing MPPT BLE Coordinator for MAC address: %s", self._mac_address)
        _LOGGER.info("Using notification-based approach")

    def notification_handler(self, sender, data):
        """Handle incoming notifications from the MPPT device."""
        try:
            _LOGGER.info("🎉 NOTIFICATION RECEIVED from %s: %d bytes", sender, len(data))
            _LOGGER.info("Raw notification data: %s", data.hex())
            
            # Check if this is a short notification (device acknowledgment)
            if len(data) < 23:
                _LOGGER.debug("Received short notification (%d bytes) - likely device acknowledgment", len(data))
                # Don't parse short notifications, but signal that we got a response
                self._notification_received.set()
                return
            
            # Parse the full MPPT data notification
            parsed_data = parse_mppt_packet(data)
            _LOGGER.info("Successfully parsed MPPT notification data: %s", parsed_data)
            
            self._latest_data = parsed_data
            self._notification_received.set()
            
            # Update the coordinator data immediately
            self.async_set_updated_data(parsed_data)
            
        except Exception as e:
            _LOGGER.error("Error parsing notification data: %s", e)
            # Still set the event so we don't timeout
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
                _LOGGER.debug("Client already connected, returning latest data")
                if self._latest_data:
                    return self._latest_data
                else:
                    # Wait a bit for notifications
                    try:
                        await asyncio.wait_for(self._notification_received.wait(), timeout=5.0)
                        return self._latest_data
                    except asyncio.TimeoutError:
                        _LOGGER.warning("No notifications received, will reconnect")

            _LOGGER.debug("Connecting to device %s", self._mac_address)
            
            # Create new client
            try:
                # Clean up any existing client first
                if self._client:
                    try:
                        await self._client.disconnect()
                    except:
                        pass
                    self._client = None
                
                self._client = BleakClient(ble_device, timeout=15.0)
                _LOGGER.debug("Created BleakClient, attempting connection...")
                await self._client.connect()
                _LOGGER.info("Connected to MPPT device %s", self._mac_address)
            except Exception as e:
                _LOGGER.error("Failed to connect to device: %s", e, exc_info=True)
                self._client = None
                raise
            
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
            
            # From nRF log: Service 0000fff0-0000-1000-8000-00805f9b34fb
            # Characteristic 0000fff1-0000-1000-8000-00805f9b34fb with notifications
            target_char_uuid = "0000fff1-0000-1000-8000-00805f9b34fb"
            
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
            write_char_uuid = "0000ffd1-0000-1000-8000-00805f9b34fb"
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
                    bytes.fromhex('FF0301000023'),  # ControllerRealdata - main real-time data command
                    bytes.fromhex('FF03010000231031'),  # Complete command with CRC
                    bytes.fromhex('FF0300FD000D'),  # NewDeviceRealdata
                    bytes.fromhex('FF030100000A'),  # OldDeviceRealdata
                ]
                
                for i, cmd in enumerate(trigger_commands):
                    try:
                        _LOGGER.info("Sending trigger command %d: %s", i+1, cmd.hex())
                        await self._client.write_gatt_char(write_char, cmd)
                        
                        # Wait for response after each command
                        self._notification_received.clear()
                        try:
                            await asyncio.wait_for(self._notification_received.wait(), timeout=5.0)
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
