from homeassistant.components.bluetooth.passive_update_coordinator import PassiveBluetoothDataUpdateCoordinator
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, SERVICE_UUID
import logging
import struct

_LOGGER = logging.getLogger(__name__)

def parse_mppt_packet(data: bytes) -> dict:
    # Example decoder based on your structure
    if len(data) < 24:
        raise ValueError("Data too short")

    solar_volt_raw = int.from_bytes(data[2:4], "little")
    solar_current_raw = int.from_bytes(data[4:6], "little")
    battery_volt_raw = int.from_bytes(data[6:8], "little")
    battery_temp_raw = int.from_bytes(data[10:12], "little")
    battery_current_raw = int.from_bytes(data[20:22], "little")
    solar_power_raw = int.from_bytes(data[22:24], "little")

    return {
        "solar_voltage": round(solar_volt_raw * 0.95, 2),
        "solar_current": round(solar_current_raw * 0.0034, 2),
        "solar_power": solar_power_raw,
        "battery_voltage": round(battery_volt_raw * 0.1, 2),
        "battery_current": round(battery_current_raw * 0.0447, 2),
        "battery_temperature": battery_temp_raw,
    }

class MPPTBLECoordinator(PassiveBluetoothDataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.data = {}

    @callback
    def handle_bluetooth_event(self, service_info: BluetoothServiceInfoBleak):
        try:
            for _, value in service_info.manufacturer_data.items():
                decoded = parse_mppt_packet(value)
                _LOGGER.debug("Decoded MPPT: %s", decoded)
                self.data = decoded
                self.async_set_updated_data(decoded)
        except Exception as e:
            raise UpdateFailed(f"Failed to parse MPPT BLE data: {e}")
