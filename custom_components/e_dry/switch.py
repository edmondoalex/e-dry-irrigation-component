
from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event
import logging

from .const import DOMAIN
from .controller import EDry2Controller

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: EDry2Controller = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    # Add a master switch that enables/disables scheduled programs
    entities.append(EDry2ProgramSwitch(controller))
    # Add per-program switches (one per configured program)
    for prog in controller.programs:
        try:
            pid = int(prog.get("id") or 0)
        except Exception:
            continue
        entities.append(EDry2ProgramItemSwitch(controller, pid, prog.get("name") or f"Programma {pid}"))
    # Add zone switches
    entities += [EDry2ZoneSwitch(controller, zone) for zone in controller.zones]
    # Add per-zone ignore-weather switches (allow toggling ignore_weather per zone)
    entities += [EDry2ZoneIgnoreWeatherSwitch(controller, zone) for zone in controller.zones]
    async_add_entities(entities)


class EDry2ProgramSwitch(SwitchEntity):
    """Master switch to enable/disable scheduled programs."""

    _attr_has_entity_name = True

    def __init__(self, controller: EDry2Controller) -> None:
        self._controller = controller
        self._attr_name = "Programmi abilitati"
        self._attr_unique_id = f"{controller.entry_id}_programs_enabled_switch"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        signal = f"{DOMAIN}_programs_updated_{self._controller.entry_id}"

        def _on_programs_update(_val=None):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("program switch: failed to schedule async_write_ha_state")

        unsub = async_dispatcher_connect(self.hass, signal, _on_programs_update)
        self.async_on_remove(unsub)

    @property
    def is_on(self) -> bool:
        return self._controller.programs_enabled()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.set_programs_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.set_programs_enabled(False)
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return attributes with available zones list for dynamic usage."""
        zones_data = []
        for z in self._controller.zones:
            zones_data.append({
                "id": int(z.get("id")),
                "name": z.get("name"),
                "physical_switch": z.get("switch_entity_id")
            })
        return {
            "available_zones": zones_data
        }


class EDry2ZoneSwitch(SwitchEntity):
    """Interruttore per una zona di irrigazione."""

    _attr_has_entity_name = True

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        self._controller = controller
        self._zone_id: int = int(zone["id"])
        self._attr_name = zone.get("name", f"Zona {self._zone_id}")
        self._attr_unique_id = f"{controller.entry_id}_zone_{self._zone_id}_switch"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        # Use a wrapper so dispatcher can pass the zone_id argument without
        # causing TypeError on the bound method.
        def _on_zone_update(_zone_id=None):
            try:
                _LOGGER.debug(
                    "switch.%s: dispatcher update received for zone %s; controller.is_zone_active=%s",
                    self._zone_id,
                    _zone_id,
                    self._controller.is_zone_active(self._zone_id),
                )
            except Exception:
                _LOGGER.exception("switch: error in dispatcher handler")
            # Ensure async_write_ha_state runs on the event loop thread
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("switch: failed to schedule async_write_ha_state thread-safely")

        unsub = async_dispatcher_connect(self.hass, signal, _on_zone_update)
        self.async_on_remove(unsub)

        # subscribe to the actual switch entity state changes so we react
        # when the relay is toggled externally
        zone = self._controller.get_zone(self._zone_id)
        entity_id = zone.get("switch_entity_id") if zone else None
        if entity_id:
            # async_track_state_change_event delivers an Event object to the
            # callback (its data contains 'entity_id', 'old_state', 'new_state').
            # Accept that Event and extract the new_state to react when the
            # physical relay is toggled directly on the device.
            def _external_switch_changed(event):
                try:
                    data = getattr(event, "data", {}) or {}
                    new_state = data.get("new_state")
                    new = new_state.state if new_state else None
                except Exception:
                    new = None

                _LOGGER.debug(
                    "switch.%s: external entity %s changed to %s (zone active=%s)",
                    self._zone_id,
                    entity_id,
                    new,
                    self._controller.is_zone_active(self._zone_id),
                )

                # If hardware turned the relay on, start the zone (and its
                # timer) so the integration stays in sync. Use
                # call_soon_threadsafe to schedule on the event loop.
                if new == "on" and not self._controller.is_zone_active(self._zone_id):
                    try:
                        self.hass.loop.call_soon_threadsafe(
                            lambda: self.hass.async_create_task(
                                self._controller.start_zone(self._zone_id)
                            )
                        )
                    except Exception:
                        _LOGGER.exception("switch: failed to schedule start_zone")

                # If hardware turned the relay off, ensure the zone is stopped
                # in the controller.
                if new == "off" and self._controller.is_zone_active(self._zone_id):
                    try:
                        self.hass.loop.call_soon_threadsafe(
                            lambda: self.hass.async_create_task(
                                self._controller.stop_zone(self._zone_id)
                            )
                        )
                    except Exception:
                        _LOGGER.exception("switch: failed to schedule stop_zone")

            unsub_state = async_track_state_change_event(
                self.hass, entity_id, _external_switch_changed
            )
            self.async_on_remove(unsub_state)

    @property
    def is_on(self) -> bool:
        return self._controller.is_zone_active(self._zone_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.start_zone(self._zone_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.stop_zone(self._zone_id)
        self.async_write_ha_state()


class EDry2ZoneIgnoreWeatherSwitch(SwitchEntity):
    """Per-zone toggle that controls whether the zone ignores weather blocking."""

    _attr_has_entity_name = True

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        self._controller = controller
        self._zone_id: int = int(zone["id"])
        # Name shown will be the entity's name and include the zone name
        zone_name = zone.get("name", f"Zona {self._zone_id}")
        self._attr_name = f"{zone_name} - ignora meteo"
        self._attr_unique_id = f"{controller.entry_id}_zone_{self._zone_id}_ignore_weather"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"

        def _on_zone_update(_zone_id=None):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("ignore_weather switch: failed to schedule async_write_ha_state")

        unsub = async_dispatcher_connect(self.hass, signal, _on_zone_update)
        self.async_on_remove(unsub)

    @property
    def is_on(self) -> bool:
        z = self._controller.get_zone(self._zone_id)
        if not z:
            return False
        return bool(z.get("ignore_weather", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            # update_zone persists options and triggers dispatcher
            await self._controller.update_zone({"zone_id": self._zone_id, "ignore_weather": True})
        except Exception:
            _LOGGER.exception("ignore_weather: failed to set true for zone %s", self._zone_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._controller.update_zone({"zone_id": self._zone_id, "ignore_weather": False})
        except Exception:
            _LOGGER.exception("ignore_weather: failed to set false for zone %s", self._zone_id)
        self.async_write_ha_state()


class EDry2ProgramItemSwitch(SwitchEntity):
    """Switch to enable/disable a single program."""

    _attr_has_entity_name = True

    def __init__(self, controller: EDry2Controller, program_id: int, name: str) -> None:
        self._controller = controller
        self._program_id = int(program_id)
        self._attr_name = name
        self._attr_unique_id = f"{controller.entry_id}_program_{self._program_id}_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        signal_specific = f"{DOMAIN}_program_updated_{self._controller.entry_id}_{self._program_id}"
        signal_generic = f"{DOMAIN}_programs_updated_{self._controller.entry_id}"

        def _on_update(_data=None):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("program item switch: failed to schedule async_write_ha_state")

        unsub1 = async_dispatcher_connect(self.hass, signal_specific, _on_update)
        unsub2 = async_dispatcher_connect(self.hass, signal_generic, _on_update)
        self.async_on_remove(unsub1)
        self.async_on_remove(unsub2)

    def _find_program(self) -> Dict[str, Any] | None:
        for p in self._controller.programs:
            try:
                if int(p.get("id", 0) or 0) == self._program_id:
                    return p
            except Exception:
                continue
        return None

    @property
    def is_on(self) -> bool:
        p = self._find_program()
        if not p:
            return False
        return bool(p.get("enabled", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.set_program_enabled(self._program_id, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.set_program_enabled(self._program_id, False)
        self.async_write_ha_state()
