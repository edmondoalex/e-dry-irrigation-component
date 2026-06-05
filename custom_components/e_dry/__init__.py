
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
import logging

from .const import DOMAIN
from .controller import EDry2Controller
from .debug import setup_debug_logger

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.NUMBER, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Ensure dedicated integration debug logger is configured early
    setup_debug_logger()

    if entry.title in ("e-dry Irrigation", "e-Dry Irrigation", "e-dry"):
        hass.config_entries.async_update_entry(entry, title="e-Dry Irrigazione")

    controller = EDry2Controller(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = controller

    # Cleanup stale entities for this config entry: compute the set of
    # desired unique_ids based on current zones/programs and remove any
    # registry entries that no longer match. This ensures that when a
    # program or zone is deleted the old entities do not remain in the
    # entity registry.
    try:
        registry = er.async_get(hass)
        desired_ids: set[str] = set()

        # Master program switch
        desired_ids.add(f"{entry.entry_id}_programs_enabled_switch")
        # Available zones sensor
        desired_ids.add(f"{entry.entry_id}_available_zones")

        # Zones
        for z in controller.zones:
            zid = int(z.get("id"))
            desired_ids.add(f"{entry.entry_id}_zone_{zid}_switch")
            desired_ids.add(f"{entry.entry_id}_zone_{zid}_duration")
            desired_ids.add(f"{entry.entry_id}_zone_{zid}_configured_duration")
            desired_ids.add(f"{entry.entry_id}_zone_{zid}_remaining")
            desired_ids.add(f"{entry.entry_id}_zone_{zid}_progress")

        # Programs
        for p in controller.programs:
            try:
                pid = int(p.get("id") or 0)
            except Exception:
                continue
            desired_ids.add(f"{entry.entry_id}_program_{pid}_schedule")
            desired_ids.add(f"{entry.entry_id}_program_{pid}_enabled")
            desired_ids.add(f"{entry.entry_id}_program_{pid}_stop")
            desired_ids.add(f"{entry.entry_id}_program_{pid}_progress")
            desired_ids.add(f"{entry.entry_id}_program_{pid}_stop")

        # Iterate registry entries for this config entry and remove any
        # whose unique_id is not in desired_ids.
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        removed = []
        for ent in list(entries):
            try:
                uid = getattr(ent, "unique_id", None)
                if not uid:
                    continue
                # unique_id may have been stored as entry_id prefix earlier
                if uid not in desired_ids:
                    registry.async_remove(ent.entity_id)
                    removed.append(ent.entity_id)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error cleaning stale entity %s", ent.entity_id)

        if removed:
            _LOGGER.info("Auto-cleaned %s stale entities for entry %s: %s", len(removed), entry.entry_id, removed)
    except Exception:
        _LOGGER.exception("Error during auto-clean of stale entities for entry %s", entry.entry_id)

    async def _options_updated(hass_: HomeAssistant, updated_entry: ConfigEntry) -> None:
        # When options change, reload the config entry so platforms and
        # entities are recreated to reflect new zones/programs.
        _LOGGER.debug("options updated for entry %s, reloading entry", updated_entry.entry_id)
        await hass_.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_options_updated))

    async def handle_start_zone(call: ServiceCall) -> None:
        await controller.start_zone(int(call.data["zone_id"]), source="manual")

    async def handle_start_zone_for(call: ServiceCall) -> None:
        await controller.start_zone_for(
            int(call.data["zone_id"]), float(call.data["minutes"]), source="manual"
        )

    async def handle_stop_zone(call: ServiceCall) -> None:
        await controller.stop_zone(int(call.data["zone_id"]))

    async def handle_skip_program_zone(call: ServiceCall) -> None:
        await controller.skip_current_program_zone()

    async def handle_stop_programs(call: ServiceCall) -> None:
        await controller.stop_programs()

    hass.services.async_register(DOMAIN, "start_zone", handle_start_zone)
    hass.services.async_register(DOMAIN, "start_zone_for", handle_start_zone_for)
    hass.services.async_register(DOMAIN, "stop_zone", handle_stop_zone)
    hass.services.async_register(DOMAIN, "skip_program_zone", handle_skip_program_zone)
    hass.services.async_register(DOMAIN, "stop_programs", handle_stop_programs)

    async def handle_update_program(call: ServiceCall) -> None:
        await controller.update_program(call.data)

    hass.services.async_register(DOMAIN, "update_program", handle_update_program)

    async def handle_create_program(call: ServiceCall) -> None:
        data = dict(call.data)
        data["program_id"] = 0
        await controller.update_program(data)

    hass.services.async_register(DOMAIN, "create_program", handle_create_program)

    async def handle_update_zone(call: ServiceCall) -> None:
        await controller.update_zone(call.data)

    hass.services.async_register(DOMAIN, "update_zone", handle_update_zone)

    async def handle_update_weather_settings(call: ServiceCall) -> None:
        await controller.update_weather_settings(dict(call.data))

    hass.services.async_register(DOMAIN, "update_weather_settings", handle_update_weather_settings)

    async def handle_update_zone_profiles(call: ServiceCall) -> None:
        await controller.update_zone_profiles(dict(call.data))

    hass.services.async_register(DOMAIN, "update_zone_profiles", handle_update_zone_profiles)

    async def handle_remove_stale_entities(call: ServiceCall) -> None:
        """Remove stale entities for this config entry matching a substring.

        Call with data: {"match": "reset", "dry_run": True} to list entities without removing.
        """
        match = (call.data.get("match") or "reset").strip().lower()
        dry_run = bool(call.data.get("dry_run"))
        if not match:
            return

        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        matched = []
        for ent in list(entries):
            try:
                name_fields = " ".join(
                    [str(getattr(ent, k, "") or "") for k in ("entity_id", "unique_id", "original_name")]
                ).lower()
                if match in name_fields:
                    matched.append(ent.entity_id)
                    if not dry_run:
                        registry.async_remove(ent.entity_id)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error processing stale entity %s", ent.entity_id)

        if dry_run:
            _LOGGER.info("remove_stale_entities (dry_run): %s entities match '%s' for entry %s: %s", len(matched), match, entry.entry_id, matched)
        else:
            _LOGGER.info("remove_stale_entities: removed %s entities matching '%s' for entry %s: %s", len(matched), match, entry.entry_id, matched)

    hass.services.async_register(DOMAIN, "remove_stale_entities", handle_remove_stale_entities)

    async def handle_request_event_log(call: ServiceCall) -> None:
        """Service to request an event log snapshot; fires bus event e_dry_event_log with payload."""
        limit = call.data.get("limit")
        events = list(controller._event_log)
        if limit:
            try:
                limit_i = int(limit)
                events = events[-limit_i:]
            except Exception:
                pass
        hass.bus.async_fire("e_dry_event_log", {"entry_id": entry.entry_id, "events": events})

    async def handle_clear_event_log(call: ServiceCall) -> None:
        controller._event_log.clear()
        # notify listeners via dispatcher (if any)
        try:
            from homeassistant.helpers.dispatcher import async_dispatcher_send

            async_dispatcher_send(hass, f"{DOMAIN}_eventlog_updated_{entry.entry_id}", None)
        except Exception:
            _LOGGER.exception("clear_event_log: failed to dispatch eventlog_updated")

    hass.services.async_register(DOMAIN, "request_event_log", handle_request_event_log)
    hass.services.async_register(DOMAIN, "clear_event_log", handle_clear_event_log)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def async_get_options_flow(config_entry: ConfigEntry):
    """Return the options flow handler for this integration.

    Home Assistant may import this from the package root; provide a
    simple factory that returns the class defined in `options_flow.py`.
    """
    try:
        from .options_flow import EDry2OptionsFlow

        return EDry2OptionsFlow(config_entry)
    except Exception:
        _LOGGER.exception("async_get_options_flow: failed to construct options flow for %s", getattr(config_entry, "entry_id", "?"))
        raise
