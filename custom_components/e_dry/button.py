from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import logging

from .const import DOMAIN
from .controller import EDry2Controller

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: EDry2Controller = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for prog in controller.programs:
        try:
            pid = int(prog.get("id") or 0)
        except Exception:
            continue
        name = prog.get("name") or f"Programma {pid}"
        entities.append(EDry2ProgramStopButton(controller, pid, name))
    async_add_entities(entities)


class EDry2ProgramStopButton(ButtonEntity):
    """Pulsante STOP per uno specifico programma."""

    _attr_icon = "mdi:stop-circle"

    def __init__(self, controller: EDry2Controller, program_id: int, name: str) -> None:
        self._controller = controller
        self._program_id = int(program_id)
        self._attr_name = f"{name} - STOP"
        self._attr_unique_id = f"{controller.entry_id}_program_{self._program_id}_stop"

    async def async_press(self) -> None:  # type: ignore[override]
        _LOGGER.debug(
            "ProgramStopButton: pressed for program %s, scheduling stop", self._program_id
        )
        try:
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(
                    self._controller.stop_program(self._program_id)
                )
                )
        except Exception:
            _LOGGER.exception("ProgramStopButton: failed to schedule stop_program task")
