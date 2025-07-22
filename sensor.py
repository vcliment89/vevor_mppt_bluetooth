"""Sensor platform for VEVOR MPPT Bluetooth integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bluetooth import MPPTBLECoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VEVOR MPPT Bluetooth sensors."""
    coordinator = MPPTBLECoordinator(hass, config_entry)
    
    # Store coordinator in hass data
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    
    sensors = [
        MPPTSensor(coordinator, config_entry, "solar_voltage", "Solar Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        MPPTSensor(coordinator, config_entry, "solar_current", "Solar Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        MPPTSensor(coordinator, config_entry, "solar_power", "Solar Power", UnitOfPower.WATT, SensorDeviceClass.POWER),
        MPPTSensor(coordinator, config_entry, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        MPPTSensor(coordinator, config_entry, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        MPPTSensor(coordinator, config_entry, "battery_temperature", "Battery Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    ]
    
    async_add_entities(sensors)


class MPPTSensor(CoordinatorEntity, SensorEntity):
    """Representation of a VEVOR MPPT sensor."""

    def __init__(
        self,
        coordinator: MPPTBLECoordinator,
        config_entry: ConfigEntry,
        sensor_key: str,
        name: str,
        unit: str,
        device_class: SensorDeviceClass,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._attr_name = f"{config_entry.data['name']} {name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{sensor_key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._config_entry = config_entry

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": self._config_entry.data["name"],
            "manufacturer": "VEVOR",
            "model": "MPPT Bluetooth Charger",
            "connections": {("mac", self._config_entry.data["mac_address"])},
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._sensor_key)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
