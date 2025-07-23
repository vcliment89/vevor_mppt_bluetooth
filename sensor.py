"""Sensor platform for VEVOR MPPT Bluetooth integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfEnergy,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bluetooth import MPPTBLECoordinator
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VEVOR MPPT Bluetooth sensors."""
    _LOGGER.info("Setting up VEVOR MPPT Bluetooth sensors for entry: %s", config_entry.entry_id)
    
    coordinator = MPPTBLECoordinator(hass, config_entry)
    
    # Store coordinator in hass data
    hass.data[DOMAIN][config_entry.entry_id] = coordinator
    
    sensors = [
        # Basic MPPT sensors
        MPPTSensor(coordinator, config_entry, "solar_voltage", "Solar Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 2),
        MPPTSensor(coordinator, config_entry, "solar_current", "Solar Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
        MPPTSensor(coordinator, config_entry, "solar_power", "Solar Power", UnitOfPower.WATT, SensorDeviceClass.POWER, 0),
        MPPTSensor(coordinator, config_entry, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 2),
        MPPTSensor(coordinator, config_entry, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
        MPPTSensor(coordinator, config_entry, "battery_temperature", "Battery Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, 1),
        
        # Load/output sensors
        MPPTSensor(coordinator, config_entry, "load_voltage", "Load Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 2),
        MPPTSensor(coordinator, config_entry, "load_current", "Load Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
        MPPTSensor(coordinator, config_entry, "load_power", "Load Power", UnitOfPower.WATT, SensorDeviceClass.POWER, 0),
        
        # Energy statistics sensors
        MPPTSensor(coordinator, config_entry, "daily_energy", "Daily Energy", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, 2),
        MPPTSensor(coordinator, config_entry, "total_energy", "Total Energy", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, 2),
        
        # Maximum value sensors
        MPPTSensor(coordinator, config_entry, "max_solar_voltage", "Max Solar Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 2),
        MPPTSensor(coordinator, config_entry, "max_battery_voltage", "Max Battery Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 2),
        MPPTSensor(coordinator, config_entry, "max_charging_current", "Max Charging Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
        
        # Status sensors (no device class for these)
        MPPTSensor(coordinator, config_entry, "charging_state", "Charging State", None, None, 0),
        MPPTSensor(coordinator, config_entry, "error_code", "Error Code", None, None, 0),
        
        # Bluetooth diagnostic sensors
        MPPTDiagnosticSensor(coordinator, config_entry, "link_quality", "Link Quality", PERCENTAGE, 0),
        MPPTDiagnosticSensor(coordinator, config_entry, "signal_strength", "Signal Strength", SIGNAL_STRENGTH_DECIBELS_MILLIWATT, 0, SensorDeviceClass.SIGNAL_STRENGTH),
    ]
    
    _LOGGER.info("Created %d MPPT sensors", len(sensors))
    async_add_entities(sensors)
    _LOGGER.info("VEVOR MPPT Bluetooth sensors setup completed")


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
        precision: int = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._attr_name = f"{config_entry.data['name']} {name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{sensor_key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if precision is not None:
            self._attr_suggested_display_precision = precision
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


class MPPTDiagnosticSensor(CoordinatorEntity, SensorEntity):
    """Representation of a VEVOR MPPT diagnostic sensor."""

    def __init__(
        self,
        coordinator: MPPTBLECoordinator,
        config_entry: ConfigEntry,
        sensor_key: str,
        name: str,
        unit: str,
        precision: int = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._attr_name = f"{config_entry.data['name']} {name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{sensor_key}"
        self._attr_native_unit_of_measurement = unit
        if device_class is not None:
            self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC  # Mark as diagnostic entity
        if precision is not None:
            self._attr_suggested_display_precision = precision
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
        return self.coordinator.data is not None and self.coordinator.data.get(self._sensor_key) is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
