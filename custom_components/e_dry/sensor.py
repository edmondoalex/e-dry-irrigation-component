
from __future__ import annotations

from datetime import timedelta, datetime
import time
from typing import Any, Dict, List
import logging
from homeassistant.util.dt import as_local

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN
from .controller import EDry2Controller, WEEKDAY_MAP

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    controller: EDry2Controller = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    entities.extend(
        EDry2ZoneRemainingSensor(controller, zone) for zone in controller.zones
    )

    entities.extend(
        EDry2ProgramInfoSensor(controller, prog) for prog in controller.programs
    )

    entities.extend(
        EDry2ProgramProgressSensor(controller, prog) for prog in controller.programs
    )

    entities.extend(
        EDry2ZoneDurationSensor(controller, zone) for zone in controller.zones
    )

    entities.extend(
        EDry2ZoneSmartDurationSensor(controller, zone) for zone in controller.zones
    )

    entities.extend(
        EDry2ZoneEffectiveDurationSensor(controller, zone) for zone in controller.zones
    )

    entities.extend(
        EDry2ZoneProgressSensor(controller, zone) for zone in controller.zones
    )

    # Add global zones sensor
    entities.append(EDry2ZonesSensor(controller))

    # Add Smart Calc sensors
    entities.append(EDry2SmartFactorSensor(controller))
    entities.append(EDry2SmartReasonSensor(controller))
    entities.append(EDry2WeatherStatusSensor(controller))

    # Add next run sensor
    entities.append(EDry2NextRunSensor(controller))

    # Add History sensors
    entities.append(EDry2DailyHistorySensor(controller))
    entities.append(EDry2WeeklyHistorySensor(controller))
    entities.append(EDry2MonthlyHistorySensor(controller))
    entities.append(EDry2YearlyHistorySensor(controller))

    # Add JSON export sensors for addon consumption
    entities.append(EDry2WeatherInfoSensor(controller))
    entities.append(EDry2ZonesInfoSensor(controller))
    entities.append(EDry2ProgramsInfoSensor(controller))
    # Event log sensor
    entities.append(EDry2EventLogSensor(controller))

    async_add_entities(entities)


class EDry2Sensor(SensorEntity):
    """Base class for E-Dry sensors with device info."""
    
    _attr_suggested_area = "GIARDINO"

    def __init__(self, controller: EDry2Controller) -> None:
        self._controller = controller

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._controller.entry_id)},
            name="Centralina Irrigazione",
            manufacturer="E-Dry",
            model="Smart Irrigation Controller",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        
        # Auto-assign Label "IRRIGAZIONE"
        try:
            l_reg = lr.async_get(self.hass)
            label_name = "IRRIGAZIONE"
            label_id = None
            
            # Find existing label by name (case-insensitive)
            for lbl in l_reg.labels.values():
                if lbl.name.lower() == label_name.lower():
                    label_id = lbl.label_id
                    break
            
            # Create if not exists
            if not label_id:
                try:
                    created = l_reg.async_create(label_name)
                    label_id = created.label_id
                except (ValueError, Exception):
                    # If creation fails (e.g. race condition), try to find it again
                    for lbl in l_reg.labels.values():
                        if lbl.name.lower() == label_name.lower():
                            label_id = lbl.label_id
                            break

            if label_id:
                e_reg = er.async_get(self.hass)
                entry = e_reg.async_get(self.entity_id)
                if entry:
                    current_labels = set(entry.labels)
                    if label_id not in current_labels:
                        current_labels.add(label_id)
                        e_reg.async_update_entity(self.entity_id, labels=current_labels)
        except Exception as e:
            _LOGGER.debug("Failed to auto-assign label: %s", e)


class EDry2WeatherInfoSensor(EDry2Sensor):
    """Aggregated weather info for addon consumption."""

    _attr_has_entity_name = False
    _attr_name = "e-dry Meteo Info"
    _attr_icon = "mdi:weather-partly-cloudy"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_weather_info"
        self._unsub = None

    @property
    def native_value(self) -> str:
        ok, _ = self._controller.get_weather_status_info()
        return "OK" if ok else "BLOCCATO"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        ok, reason = self._controller.get_weather_status_info()
        smart_factor, smart_reason = self._controller.get_smart_calc_info()
        irrigation_weather = self._controller._get_irrigation_weather_payload()
        # include list/count of enabled programs for convenience (Italian key 'programmi_abilitati')
        enabled_programs = [int(p.get("id")) for p in (self._controller.programs or []) if p.get("enabled")]
        attrs = {
            "status": "OK" if ok else "BLOCCATO",
            "reason": reason,
            "is_blocking": not ok,
            "weather_mode": "e_sunmind_irrigation_api" if irrigation_weather else "legacy_ha_sensors",
            "esunmind_weather_api_url": self._controller._esunmind_weather_api_url,
            "weather_max_age_seconds": float(self._controller._weather_max_age_seconds or 0.0),
            "forecast_rain_skip_mm": float(self._controller._forecast_rain_skip_mm or 0.0),
            "recent_rain_skip_mm": float(self._controller._recent_rain_skip_mm or 0.0),
            "weather_api_error": self._controller._irrigation_weather_error,
            "rain_sensor": self._controller._rain_sensor,
            "rain_threshold": float(self._controller._rain_threshold or 0.0),
            "temp_sensor": self._controller._temp_sensor,
            "min_temp": float(self._controller._min_temp or 0.0),
            "hum_sensor": self._controller._hum_sensor,
            "wind_sensor": self._controller._wind_sensor,
            "wind_threshold": float(self._controller._wind_threshold or 0.0),
            "smart_calc_enabled": bool(self._controller._enable_smart_calc),
            "smart_factor": round(smart_factor, 3),
            "smart_reason": smart_reason,
            "manual_adjustment_percent": float(self._controller.get_manual_adjustment()),
            "programmi_abilitati": enabled_programs,
            "programmi_abilitati_count": len(enabled_programs),
        }
        if irrigation_weather:
            attrs.update({
                "source": irrigation_weather.get("source"),
                "age_seconds": irrigation_weather.get("age_seconds"),
                "available": irrigation_weather.get("available"),
                "rain_block": irrigation_weather.get("rain_block"),
                "wind_block": irrigation_weather.get("wind_block"),
                "freeze_block": irrigation_weather.get("freeze_block"),
                "is_raining": irrigation_weather.get("is_raining"),
                "rain_last_24h_mm": irrigation_weather.get("rain_last_24h_mm"),
                "forecast_rain_24h_mm": irrigation_weather.get("forecast_rain_24h_mm"),
                "et0_mm_day": irrigation_weather.get("et0_mm_day"),
                "irrigation_weather_score": irrigation_weather.get("irrigation_weather_score"),
                "irrigation_weather_reason": irrigation_weather.get("irrigation_weather_reason"),
            })
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Update every 60 seconds
        self._unsub = async_track_time_interval(self.hass, self._update, timedelta(seconds=60))
        if self._unsub:
            self.async_on_remove(self._unsub)

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2ZonesInfoSensor(EDry2Sensor):
    """Expose full zones data as attributes for addon consumption."""

    _attr_has_entity_name = False
    _attr_name = "e-dry Zones Info"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_zones_info"
        self._signal_unsub = None

    @property
    def native_value(self) -> int:
        return len(self._controller.zones)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        zones_out: List[Dict[str, Any]] = []
        smart_factor, _ = self._controller.get_smart_calc_info()
        # manual adjustment factor (percentage -> factor)
        manual_factor = float(self._controller.get_manual_adjustment()) / 100.0
        for z in self._controller.zones:
            zid = int(z.get("id"))
            base = float(z.get("base_minutes", 0.0))
            configured = float(self._controller.get_zone_duration(zid) or 0.0)
            # If zone ignores weather, smart duration == configured (no smart applied)
            zone_ignores = bool(z.get("ignore_weather", False))
            smart_d = round(configured * (1.0 if zone_ignores else smart_factor), 1)
            # Effective duration: always apply manual factor; apply smart only if not ignored
            zone_smart = 1.0 if zone_ignores else smart_factor
            effective_d = round(configured * manual_factor * zone_smart, 1)
            end_ts = z.get("end_ts")
            remaining = None
            try:
                if end_ts:
                    remaining = int(max(0, end_ts - time.time()))
                else:
                    remaining = 0
            except Exception:
                remaining = 0

            zones_out.append({
                "id": zid,
                "name": z.get("name"),
                "unique_id_prefix": f"{self._controller.entry_id}_zone_{zid}",
                "switch_entity_id": z.get("switch_entity_id"),
                "base_minutes": base,
                "configured_duration": configured,
                "smart_duration": smart_d,
                "effective_duration": effective_d,
                "ignore_weather": bool(z.get("ignore_weather", False)),
                "active": bool(z.get("active", False)),
                "end_ts": z.get("end_ts"),
                "remaining_seconds": remaining,
            })

        # also include enabled programs summary for convenience
        enabled_programs = [int(p.get("id")) for p in (self._controller.programs or []) if p.get("enabled")]
        return {"zones": zones_out, "programmi_abilitati": enabled_programs, "programmi_abilitati_count": len(enabled_programs)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"

        def _on_zone_update(*_):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("zones info: failed to schedule async_write_ha_state thread-safely")

        self._signal_unsub = async_dispatcher_connect(self.hass, signal, _on_zone_update)
        if self._signal_unsub:
            self.async_on_remove(self._signal_unsub)


class EDry2ProgramsInfoSensor(EDry2Sensor):
    """Expose full programs data as attributes for addon consumption."""

    _attr_has_entity_name = False
    _attr_name = "e-dry Programs Info"
    _attr_icon = "mdi:calendar-multiple"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_programs_info"
        self._signal_unsub = None

    def _program_next_run(self, prog: Dict[str, Any]) -> Any:
        # compute next run for this program (similar logic to controller.get_next_scheduled_run)
        now = as_local(datetime.now())
        time_str = prog.get("time", "08:00")
        try:
            hh, mm = map(int, time_str.split(":"))
        except Exception:
            return None

        weekdays = prog.get("weekdays", set()) or set()
        if not weekdays:
            return None

        for d in range(8):
            check_date = now.date() + timedelta(days=d)
            candidate = datetime(
                check_date.year, check_date.month, check_date.day, hh, mm, 0, 0, tzinfo=now.tzinfo
            )
            if candidate <= now:
                continue
            if check_date.weekday() in weekdays:
                return candidate
        return None

    @property
    def native_value(self) -> int:
        return len(self._controller.programs)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        progs_out: List[Dict[str, Any]] = []
        for p in self._controller.programs:
            try:
                pid = int(p.get("id", 0) or 0)
            except Exception:
                continue

            next_run = self._program_next_run(p)
            progress = 0
            if self._controller._current_program_id == pid:
                start_ts = self._controller._current_program_start_ts
                duration = self._controller._current_program_duration
                if start_ts and duration and duration > 0:
                    elapsed = time.time() - start_ts
                    progress = int(min(100, max(0, (elapsed / duration) * 100)))

            progs_out.append({
                "id": pid,
                "name": p.get("name"),
                "enabled": bool(p.get("enabled", False)),
                "time": p.get("time"),
                "days": list(p.get("days", []) or []),
                "pause_minutes": float(p.get("pause_minutes", 0) or 0),
                "zones": list(p.get("zones", []) or []),
                "zone_durations": p.get("zone_durations", {}) or {},
                "next_run": next_run,
                "progress_percent": progress,
            })

        return {"programs": progs_out}
        # unreachable: keep for clarity

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_programs_updated_{self._controller.entry_id}"

        def _on_programs_update(*_):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("programs info: failed to schedule async_write_ha_state thread-safely")

        self._signal_unsub = async_dispatcher_connect(self.hass, signal, _on_programs_update)
        if self._signal_unsub:
            self.async_on_remove(self._signal_unsub)


class EDry2EventLogSensor(EDry2Sensor):
    """Expose the in-memory event log for addon consumption."""

    _attr_has_entity_name = False
    _attr_name = "e-dry Event Log"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_event_log"
        self._signal_unsub = None

    @property
    def native_value(self) -> int:
        return len(self._controller._event_log)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        # return events as list (oldest first). Add count for convenience
        events = list(self._controller._event_log)
        return {"events": events, "events_count": len(events)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_eventlog_updated_{self._controller.entry_id}"

        def _on_eventlog_update(*_):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("eventlog sensor: failed to schedule async_write_ha_state")

        self._signal_unsub = async_dispatcher_connect(self.hass, signal, _on_eventlog_update)
        if self._signal_unsub:
            self.async_on_remove(self._signal_unsub)


class EDry2ZoneRemainingSensor(EDry2Sensor):
    """Tempo rimanente per una zona attiva."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "s"

    def __init__(self, controller: EDry2Controller, zone: Dict[str, Any]) -> None:
        super().__init__(controller)
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - remaining"
        self._attr_unique_id = (
            f"{controller.entry_id}_zone_{self._zone_id}_remaining"
        )
        self._unsub = None
        self._signal_unsub = None

    @property
    def native_value(self) -> int | None:
        z = self._controller.get_zone(self._zone_id)
        if not z:
            return None
        end_ts = z.get("end_ts")
        if end_ts is None:
            return 0
        rem = int(max(0, end_ts - time.time()))
        return rem

    async def _async_update_tick(self, now) -> None:
        self.async_write_ha_state()

    def _on_zone_update(self, zone_id) -> None:
        try:
            zid = int(zone_id)
        except Exception:
            return
        if zid == self._zone_id:
            _LOGGER.debug(
                "remaining_sensor.%s: dispatcher update for zone %s; native_value=%s",
                self._zone_id,
                zone_id,
                self.native_value,
            )
            try:
                # schedule state update thread-safely
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("remaining_sensor: failed to schedule async_write_ha_state thread-safely")

    @property
    def state(self) -> str | None:
        val = self.native_value
        if val is None:
            return None
        mm = val // 60
        ss = val % 60
        return f"{mm:02d}:{ss:02d}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._async_update_tick, timedelta(seconds=1)
        )
        # subscribe to controller updates for immediate refresh
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        self._signal_unsub = async_dispatcher_connect(self.hass, signal, self._on_zone_update)
        # register the unsubscribe callbacks directly so Home Assistant
        # calls them once when the entity is removed (avoid double-unsubscribe)
        if self._unsub:
            self.async_on_remove(self._unsub)
        if self._signal_unsub:
            self.async_on_remove(self._signal_unsub)



class EDry2ProgramInfoSensor(EDry2Sensor):
    """Sensore con orario / giorni / zone di un programma."""

    _attr_has_entity_name = True

    _DAY_LABEL = {
        "mon": "Lunedì",
        "tue": "Martedì",
        "wed": "Mercoledì",
        "thu": "Giovedì",
        "fri": "Venerdì",
        "sat": "Sabato",
        "sun": "Domenica",
    }

    def __init__(self, controller: EDry2Controller, program: Dict[str, Any]) -> None:
        super().__init__(controller)
        self._program_id: int = int(program.get("id", 0) or 0)
        name = program.get("name", f"Programma {self._program_id}")
        self._attr_name = f"{name} - schedule"
        self._attr_unique_id = (
            f"{controller.entry_id}_program_{self._program_id}_schedule"
        )
        self._signal_unsub = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal_specific = f"{DOMAIN}_program_updated_{self._controller.entry_id}_{self._program_id}"
        signal_generic = f"{DOMAIN}_programs_updated_{self._controller.entry_id}"

        def _on_program_update(_data=None):
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("program sensor: failed to schedule async_write_ha_state")

        unsub1 = async_dispatcher_connect(self.hass, signal_specific, _on_program_update)
        unsub2 = async_dispatcher_connect(self.hass, signal_generic, _on_program_update)
        self._signal_unsub = (unsub1, unsub2)
        self.async_on_remove(lambda: unsub1())
        self.async_on_remove(lambda: unsub2())

    def _find_program(self) -> Dict[str, Any] | None:
        for p in self._controller.programs:
            try:
                if int(p.get("id", 0) or 0) == self._program_id:
                    return p
            except (TypeError, ValueError):
                continue
        return None

    @property
    def native_value(self) -> str | None:
        prog = self._find_program()
        if not prog:
            return None
        if not prog.get("enabled", False):
            return "disabled"
        return prog.get("time") or None

    @property
    def extra_state_attributes(self) -> Dict[str, Any] | None:
        prog = self._find_program()
        if not prog:
            return None

        weekdays = prog.get("weekdays", set()) or set()
        idx_to_code = {v: k for k, v in WEEKDAY_MAP.items()}

        day_codes: List[str] = []
        day_labels: List[str] = []
        for idx in sorted(weekdays):
            code = idx_to_code.get(idx)
            if not code:
                continue
            day_codes.append(code)
            day_labels.append(self._DAY_LABEL.get(code, code))

        zone_ids: List[int] = prog.get("zones", []) or []
        zone_names: List[str] = []
        for zid in zone_ids:
            z = self._controller.get_zone(zid)
            if z:
                zone_names.append(z.get("name", f"Zona {zid}"))

        return {
            "program_id": self._program_id,
            "enabled": bool(prog.get("enabled", False)),
            "time": prog.get("time"),
            "days": day_codes,
            "days_friendly": day_labels,
            "zones": zone_ids,
            "zones_names": zone_names,
            "pause_minutes": prog.get("pause_minutes", 0),
        }

class EDry2ZoneDurationSensor(EDry2Sensor):
    """Sensore con durata configurata per la zona (minuti)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        super().__init__(controller)
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - durata configurata"
        self._attr_unique_id = f"{controller.entry_id}_zone_{self._zone_id}_configured_duration"
        self._unsub = None

    @property
    def native_value(self) -> float | None:
        return self._controller.get_zone_duration(self._zone_id)

    def _on_zone_update(self, zone_id):
        try:
            zid = int(zone_id)
        except Exception:
            return
        if zid == self._zone_id:
            # schedule state write on event loop thread
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                _LOGGER.exception("duration_sensor: failed to schedule async_write_ha_state thread-safely")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        unsub = async_dispatcher_connect(self.hass, signal, self._on_zone_update)
        self.async_on_remove(unsub)
        # ensure initial state is published
        self.async_write_ha_state()


class EDry2ZoneSmartDurationSensor(EDry2Sensor):
    """Sensore con durata basata SOLO su Meteo (Smart) per la zona (minuti)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:weather-cloudy-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        super().__init__(controller)
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - durata meteo"
        self._attr_unique_id = f"{controller.entry_id}_zone_{self._zone_id}_smart_duration"

    @property
    def native_value(self) -> float | None:
        base = self._controller.get_zone_duration(self._zone_id)
        smart_factor, _ = self._controller.get_smart_calc_info()
        return round(base * smart_factor, 1)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._update, timedelta(minutes=5)
        )
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._update)
        )

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2ZoneEffectiveDurationSensor(EDry2Sensor):
    """Sensore con durata FINALE (Smart + Manuale) per la zona (minuti)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:clock-fast"

    def __init__(self, controller: EDry2Controller, zone: dict) -> None:
        super().__init__(controller)
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - durata effettiva"
        self._attr_unique_id = f"{controller.entry_id}_zone_{self._zone_id}_effective_duration"

    @property
    def native_value(self) -> float | None:
        base = self._controller.get_zone_duration(self._zone_id)
        factor = self._controller._get_adjustment_factor()
        return round(base * factor, 1)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._update, timedelta(minutes=5)
        )
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._update)
        )
        # Also update when global adjustment changes
        signal_adj = f"{DOMAIN}_adjustment_updated_{self._controller.entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_adj, self._update)
        )

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2ZoneProgressSensor(EDry2Sensor):
    """Sensore percentuale completamento irrigazione zona."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:water-percent"

    def __init__(self, controller: EDry2Controller, zone: Dict[str, Any]) -> None:
        super().__init__(controller)
        self._zone_id: int = int(zone["id"])
        self._attr_name = f"{zone.get('name', f'Zona {self._zone_id}')} - progresso"
        self._attr_unique_id = (
            f"{controller.entry_id}_zone_{self._zone_id}_progress"
        )
        self._unsub = None
        self._signal_unsub = None

    @property
    def native_value(self) -> int | None:
        z = self._controller.get_zone(self._zone_id)
        if not z:
            return 0
        
        start_ts = z.get("start_ts")
        end_ts = z.get("end_ts")
        
        if not start_ts or not end_ts:
            return 0
            
        now = time.time()
        if now >= end_ts:
            return 100
        if now <= start_ts:
            return 0
            
        total = end_ts - start_ts
        if total <= 0:
            return 0
            
        elapsed = now - start_ts
        pct = (elapsed / total) * 100.0
        return int(min(100, max(0, pct)))

    async def _async_update_tick(self, now) -> None:
        self.async_write_ha_state()

    def _on_zone_update(self, zone_id) -> None:
        try:
            zid = int(zone_id)
        except Exception:
            return
        if zid == self._zone_id:
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception:
                pass

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Aggiorna ogni 5 secondi per non sovraccaricare troppo, o 1 secondo se preferisci fluidità
        self._unsub = async_track_time_interval(
            self.hass, self._async_update_tick, timedelta(seconds=5)
        )
        signal = f"{DOMAIN}_zone_updated_{self._controller.entry_id}"
        self._signal_unsub = async_dispatcher_connect(self.hass, signal, self._on_zone_update)
        
        if self._unsub:
            self.async_on_remove(self._unsub)
        if self._signal_unsub:
            self.async_on_remove(self._signal_unsub)

        self.async_write_ha_state()


class EDry2ProgramProgressSensor(EDry2Sensor):
    """Sensore percentuale completamento programma."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:progress-clock"

    def __init__(self, controller: EDry2Controller, prog: Dict[str, Any]) -> None:
        super().__init__(controller)
        self._program_id: int = int(prog.get("id") or 0)
        self._attr_name = f"{prog.get('name', f'Programma {self._program_id}')} - progresso"
        self._attr_unique_id = (
            f"{controller.entry_id}_program_{self._program_id}_progress"
        )
        self._unsub = None

    @property
    def native_value(self) -> int | None:
        # Se il programma corrente non è questo, 0%
        if self._controller._current_program_id != self._program_id:
            return 0
        
        start_ts = self._controller._current_program_start_ts
        duration = self._controller._current_program_duration
        
        if not start_ts or not duration or duration <= 0:
            return 0
            
        now = time.time()
        elapsed = now - start_ts
        pct = (elapsed / duration) * 100.0
        return int(min(100, max(0, pct)))

    async def _async_update_tick(self, now) -> None:
        # Aggiorna solo se questo programma è in esecuzione
        if self._controller._current_program_id == self._program_id:
            self.async_write_ha_state()
        else:
            # Se non è in esecuzione ma lo stato non è 0, aggiorna per resettare a 0
            if self.state != "0":
                self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Aggiorna ogni 5 secondi
        self._unsub = async_track_time_interval(
            self.hass, self._async_update_tick, timedelta(seconds=5)
        )
        if self._unsub:
            self.async_on_remove(self._unsub)


class EDry2ZonesSensor(EDry2Sensor):
    """Sensore che espone la lista delle zone disponibili come attributo."""

    _attr_has_entity_name = False
    _attr_name = "Zone e-dry Disponibili"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_available_zones"

    @property
    def native_value(self) -> int:
        """Restituisce il numero di zone configurate."""
        return len(self._controller.zones)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Restituisce la lista dettagliata delle zone."""
        zones_data = []
        for z in self._controller.zones:
            zones_data.append({
                "id": int(z.get("id")),
                "name": z.get("name"),
                "switch_entity_id": z.get("switch_entity_id")
            })
        return {
            "zones": zones_data
        }


class EDry2SmartFactorSensor(EDry2Sensor):
    """Sensore che mostra il fattore di moltiplicazione Smart Calc."""
    _attr_has_entity_name = False
    _attr_name = "Fattore Smart Calc"
    _attr_icon = "mdi:calculator"
    _attr_state_class = "measurement"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_smart_factor"

    @property
    def native_value(self) -> float:
        factor, _ = self._controller.get_smart_calc_info()
        return round(factor, 2)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._update, timedelta(minutes=5)
        )

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2SmartReasonSensor(EDry2Sensor):
    """Sensore che spiega il calcolo Smart Calc."""
    _attr_has_entity_name = False
    _attr_name = "Ragionamento Smart Calc"
    _attr_icon = "mdi:text-box-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_smart_reason"

    @property
    def native_value(self) -> str:
        _, reason = self._controller.get_smart_calc_info()
        return reason

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._update, timedelta(minutes=5)
        )

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2WeatherStatusSensor(EDry2Sensor):
    """Sensore stato meteo (OK o Bloccato)."""
    _attr_has_entity_name = False
    _attr_name = "Stato Meteo Irrigazione"
    _attr_icon = "mdi:weather-partly-cloudy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_weather_status"

    @property
    def native_value(self) -> str:
        ok, reason = self._controller.get_weather_status_info()
        return "OK" if ok else "BLOCCATO"

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        ok, reason = self._controller.get_weather_status_info()
        return {
            "reason": reason,
            "is_blocking": not ok
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub = async_track_time_interval(
            self.hass, self._update, timedelta(minutes=5)
        )

    async def _update(self, *args):
        self.async_write_ha_state()


class EDry2NextRunSensor(EDry2Sensor):
    """Sensore prossima irrigazione programmata."""

    _attr_has_entity_name = False
    _attr_name = "Prossima Irrigazione"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = "timestamp"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_unique_id = f"{controller.entry_id}_next_run"
        self._unsub = None

    @property
    def native_value(self) -> datetime | None:
        return self._controller.get_next_scheduled_run()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Update every minute to keep it fresh
        self._unsub = async_track_time_interval(
            self.hass, self._async_update_tick, timedelta(minutes=1)
        )
        
        # Also listen for program updates
        signal = f"{DOMAIN}_programs_updated_{self._controller.entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._async_update_tick)
        )

    async def _async_update_tick(self, *args) -> None:
        self.async_write_ha_state()


class EDry2HistorySensor(RestoreEntity, EDry2Sensor):
    """Base class for history sensors."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:chart-histogram"
    _attr_state_class = "total_increasing"

    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._state = 0.0

    @property
    def native_value(self) -> float:
        return round(self._state, 1)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        
        # Restore state
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._state = float(state.state)
            except ValueError:
                self._state = 0.0

        # Listen for irrigation finished events
        signal = f"{DOMAIN}_irrigation_finished_{self._controller.entry_id}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._on_irrigation_finished)
        )
        
        self._setup_reset_listener()

    def _on_irrigation_finished(self, duration_sec):
        minutes = float(duration_sec) / 60.0
        self._state += minutes
        # Schedule state write safely on the event loop in case this
        # callback is invoked from a worker thread.
        try:
            self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
        except Exception:
            _LOGGER.exception("_on_irrigation_finished: failed to schedule async_write_ha_state thread-safely")

    def _setup_reset_listener(self):
        raise NotImplementedError

    async def _reset(self, *args):
        self._state = 0.0
        self.async_write_ha_state()


class EDry2DailyHistorySensor(EDry2HistorySensor):
    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_name = "Statistiche: Oggi"
        self._attr_unique_id = f"{controller.entry_id}_history_daily"

    def _setup_reset_listener(self):
        # Reset every day at midnight
        self.async_on_remove(
            async_track_time_change(self.hass, self._reset, hour=0, minute=0, second=0)
        )


class EDry2WeeklyHistorySensor(EDry2HistorySensor):
    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_name = "Statistiche: Settimana"
        self._attr_unique_id = f"{controller.entry_id}_history_weekly"

    def _setup_reset_listener(self):
        # Reset every day at midnight, check if it's Monday
        self.async_on_remove(
            async_track_time_change(self.hass, self._check_reset, hour=0, minute=0, second=0)
        )

    async def _check_reset(self, now):
        if now.weekday() == 0: # Monday
            await self._reset()


class EDry2MonthlyHistorySensor(EDry2HistorySensor):
    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_name = "Statistiche: Mese"
        self._attr_unique_id = f"{controller.entry_id}_history_monthly"

    def _setup_reset_listener(self):
        self.async_on_remove(
            async_track_time_change(self.hass, self._check_reset, hour=0, minute=0, second=0)
        )

    async def _check_reset(self, now):
        if now.day == 1:
            await self._reset()


class EDry2YearlyHistorySensor(EDry2HistorySensor):
    def __init__(self, controller: EDry2Controller) -> None:
        super().__init__(controller)
        self._attr_name = "Statistiche: Anno"
        self._attr_unique_id = f"{controller.entry_id}_history_yearly"

    def _setup_reset_listener(self):
        self.async_on_remove(
            async_track_time_change(self.hass, self._check_reset, hour=0, minute=0, second=0)
        )

    async def _check_reset(self, now):
        if now.day == 1 and now.month == 1:
            await self._reset()



