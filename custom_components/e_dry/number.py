
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .controller import EDry2Controller


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: EDry2Controller = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        EDry2ZoneDurationNumber(controller, zone) for zone in controller.zones
    ]
    # Add global adjustment number
    entities.append(EDry2GlobalAdjustmentNumber(controller))
    async_add_entities(entities)


class EDry2GlobalAdjustmentNumber(NumberEntity, RestoreEntity):
    """Slider per regolazione globale percentuale (0-200%)."""

    _attr_has_entity_name = False
    _attr_name = "Regolazione Stagionale"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 200.0
    _attr_native_step = 10.0
    _attr_icon = "mdi:water-percent"

    def __init__(self, controller: EDry2Controller) -> None:
        self._controller = controller
        self._attr_unique_id = f"{controller.entry_id}_global_adjustment"
        self._attr_native_value = 100.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable", ""):
            try:
                val = float(last.state)
                self._attr_native_value = val
                self._controller.set_manual_adjustment(val)
            except ValueError:
                pass
        else:
            # Ensure controller has default
            self._controller.set_manual_adjustment(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._controller.set_manual_adjustment(value)
        self.async_write_ha_state()


class EDry2ZoneDurationNumber(NumberEntity, RestoreEntity):
    """Slider di durata per ogni zona (minuti)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    # Range richiesto: 1 - 30 minuti
    _attr_native_min_value = 1.0
    _attr_native_max_value = 30.0
    _attr_native_step = 1.0

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        self._controller = controller
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - durata"
        self._attr_unique_id = (
            f"{controller.entry_id}_zone_{self._zone_id}_duration"
        )
        self._attr_native_value = controller.get_zone_duration(self._zone_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable", ""):
            try:
                value = float(last.state)
            except (TypeError, ValueError):
                value = self._attr_native_value
            if value is not None:
                await self.async_set_native_value(value)

    async def async_set_native_value(self, value: float) -> None:
        if value < self._attr_native_min_value:
            value = self._attr_native_min_value
        if value > self._attr_native_max_value:
            value = self._attr_native_max_value

        self._attr_native_value = value
        self._controller.set_zone_duration(self._zone_id, value)
        self.async_write_ha_state()
