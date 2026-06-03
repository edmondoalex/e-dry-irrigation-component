from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict, Optional, List, Callable, Set, Tuple
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.util.dt import as_local
from homeassistant.helpers.dispatcher import async_dispatcher_send
from collections import deque
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
try:
    # Ensure integration file logger exists for runtime debugging
    from .debug import setup_debug_logger  # type: ignore

    setup_debug_logger()
except Exception:
    _LOGGER.debug("controller: could not initialize file logger")

# Default minutes to use when a zone is activated externally without
# a configured duration.
DEFAULT_ZONE_MINUTES = 10.0
MASTER_POST_SECONDS = 3
DEFAULT_ESUNMIND_IRRIGATION_URL = "http://192.168.3.24:1980/api/weather/irrigation"

BUILTIN_ZONE_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": {"id": "standard", "name": "Standard", "smart_multiplier": 1.0, "wind_sensitive": True},
    "erba": {"id": "erba", "name": "Erba / prato", "smart_multiplier": 1.15, "wind_sensitive": True},
    "fiori": {"id": "fiori", "name": "Fiori / aiuole", "smart_multiplier": 1.05, "wind_sensitive": True},
    "piante": {"id": "piante", "name": "Piante / siepi", "smart_multiplier": 0.90, "wind_sensitive": False},
    "orto": {"id": "orto", "name": "Orto", "smart_multiplier": 1.20, "wind_sensitive": False},
    "vasi": {"id": "vasi", "name": "Vasi", "smart_multiplier": 1.30, "wind_sensitive": False},
    "alberi": {"id": "alberi", "name": "Alberi", "smart_multiplier": 0.75, "wind_sensitive": False},
}

WEEKDAY_MAP: Dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class EDry2Controller:
    """Core logico per l'irrigazione e-dry."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self._hass = hass
        self._entry = entry
        self._options: Dict[str, Any] = dict(entry.options)

        # Event log (in-memory, auto-trims oldest entries when capacity exceeded)
        # Default capacity set to 500 (can be adjusted if needed)
        self._event_log: deque[Dict[str, Any]] = deque(maxlen=500)

        # Persistent storage for event log (optional persistence across HA restarts)
        # Uses Home Assistant Storage helper to keep events under .storage
        try:
            self._store = Store(self._hass, 1, f"{DOMAIN}_{self._entry.entry_id}_event_log")
            # Schedule async load so __init__ remains sync
            self._hass.async_create_task(self._async_load_event_log())
        except Exception:
            _LOGGER.exception("controller: failed to init event log storage")

        self._zones: Dict[int, Dict[str, Any]] = {}
        self._programs: List[Dict[str, Any]] = []

        # Task used to track a running program so it can be cancelled.
        self._program_task: Optional[asyncio.Task] = None
        # Master flag: whether scheduled programs are allowed to run.
        self._programs_enabled: bool = True
        # Currently running program id (if any)
        self._current_program_id: Optional[int] = None
        self._current_program_start_ts: Optional[float] = None
        self._current_program_duration: Optional[float] = None

        # When a program explicitly controls the master valve, set this
        # flag to prevent automatic updates from `_update_master_switch`.
        self._master_lock: bool = False

        self.reload_options(self._options)

        self._irrigation_weather_cache: Dict[str, Any] = {}
        self._irrigation_weather_cache_ts: float = 0.0
        self._irrigation_weather_error: Optional[str] = None
        self._hass.async_create_task(self.async_refresh_irrigation_weather())
        self._weather_interval_listener = async_track_time_interval(
            hass, self._handle_weather_refresh_tick, timedelta(minutes=5)
        )

        self._time_listener = async_track_time_change(
            hass, self._handle_time_tick, second=0
        )

    @property
    def entry_id(self) -> str:
        return self._entry.entry_id

    @property
    def zones(self) -> List[Dict[str, Any]]:
        return list(self._zones.values())

    @property
    def programs(self) -> List[Dict[str, Any]]:
        return list(self._programs)

    def get_zone(self, zone_id: int) -> Optional[Dict[str, Any]]:
        return self._zones.get(zone_id)

    def is_zone_active(self, zone_id: int) -> bool:
        zone = self.get_zone(zone_id)
        if not zone:
            return False
        return bool(zone.get("active"))

    def get_zone_duration(self, zone_id: int) -> float:
        zone = self.get_zone(zone_id)
        if not zone:
            return 0.0
        return float(zone.get("base_minutes", 0.0))

    def _now_iso(self) -> str:
        # Use Home Assistant local time for log timestamps so they match UI
        try:
            return as_local(datetime.now(timezone.utc)).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    def log_event(self, ev_type: str, message: str, details: dict | None = None) -> None:
        """Append an event to the in-memory event log and notify listeners.

        This will append a dict with keys: ts, type, message, details.
        It dispatches a dispatcher signal for internal sensors and fires a
        HA bus event `e_dry_event_log` so addons can listen via WebSocket.
        """
        ev = {
            "ts": self._now_iso(),
            "type": ev_type,
            "message": message,
            "details": details or {},
        }
        try:
            self._event_log.append(ev)
        except Exception:
            _LOGGER.exception("log_event: failed to append event")

        # Persist the event log asynchronously (best-effort)
        try:
            # Schedule save task; do not await here to avoid blocking
            self._hass.async_create_task(self._async_save_event_log())
        except Exception:
            _LOGGER.exception("log_event: failed to schedule save of event log")

        # Notify internal sensors/listeners via dispatcher
        try:
            async_dispatcher_send(self._hass, f"{DOMAIN}_eventlog_updated_{self._entry.entry_id}", ev)
        except Exception:
            _LOGGER.exception("log_event: failed to dispatch eventlog_updated")

        # Fire a bus event so addons (WS clients) can receive it immediately
        try:
            self._hass.bus.async_fire("e_dry_event_log", {"entry_id": self._entry.entry_id, "event": ev})
        except Exception:
            _LOGGER.exception("log_event: failed to fire e_dry_event_log on bus")

    async def _async_load_event_log(self) -> None:
        """Load persisted event log from storage (if present)."""
        try:
            data = await self._store.async_load()
            if data and isinstance(data.get("events"), list):
                # Replace in-memory deque preserving maxlen
                self._event_log = deque(data.get("events"), maxlen=500)
                _LOGGER.debug("Loaded %s events from persistent storage", len(self._event_log))
        except Exception:
            _LOGGER.exception("_async_load_event_log: failed to load event log from storage")

    async def _async_save_event_log(self) -> None:
        """Persist current event log to storage (best-effort)."""
        try:
            # Convert deque to list for serialization
            payload = {"events": list(self._event_log)}
            await self._store.async_save(payload)
        except Exception:
            _LOGGER.exception("_async_save_event_log: failed to persist event log")

    def set_zone_duration(self, zone_id: int, minutes: float) -> None:
        zone = self.get_zone(zone_id)
        if not zone:
            return
        zone["base_minutes"] = float(minutes)
        # Notify listeners that zone data changed (duration updated)
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_zone_updated_{self._entry.entry_id}", zone_id
        )

    def reload_options(self, options: Dict[str, Any]) -> None:
        self._options = dict(options)
        self._master_switch_entity_id = self._options.get("master_switch_entity_id")
        
        # Weather settings
        self._rain_sensor = self._options.get("rain_sensor_entity_id") or "sensor.e_sunmind_weather_precip_1h_mm"
        self._rain_threshold = float(self._options.get("rain_threshold") or 0.0)
        self._temp_sensor = self._options.get("temp_sensor_entity_id") or "sensor.e_sunmind_weather_temp_c"
        self._min_temp = float(self._options.get("min_temp") or 5.0)
        self._hum_sensor = self._options.get("humidity_sensor_entity_id") or "sensor.e_sunmind_weather_humidity_pct"
        self._wind_sensor = self._options.get("wind_sensor_entity_id") or "sensor.e_sunmind_weather_wind_ms"
        self._wind_threshold = float(self._options.get("wind_threshold") or 20.0)
        self._enable_smart_calc = bool(self._options.get("enable_smart_calc", False))
        self._esunmind_weather_api_url = self._normalize_esunmind_weather_url(
            self._options.get("esunmind_weather_api_url")
            or self._options.get("e_sunmind_irrigation_api_url")
            or DEFAULT_ESUNMIND_IRRIGATION_URL
        )
        self._weather_max_age_seconds = float(self._options.get("weather_max_age_seconds") or 900.0)
        self._forecast_rain_skip_mm = float(self._options.get("forecast_rain_skip_mm") or 6.0)
        self._recent_rain_skip_mm = float(self._options.get("recent_rain_skip_mm") or 4.0)
        self._custom_zone_profiles = self._normalize_custom_profiles(self._options.get("custom_zone_profiles") or [])
        
        # Manual adjustment (default 100%)
        # We don't load this from options because it's a runtime state managed by a NumberEntity
        if not hasattr(self, "_manual_adjustment"):
            self._manual_adjustment = 100.0

        self._zones.clear()
        zones_opt = self._options.get("zones", []) or []
        for item in zones_opt:
            try:
                zid = int(item.get("id"))
            except (TypeError, ValueError):
                continue

            self._zones[zid] = {
                "id": zid,
                "name": item.get("name", f"Zona {zid}"),
                "switch_entity_id": item.get("switch_entity_id"),
                "base_minutes": float(item.get("base_minutes", 10)),
                "ignore_weather": bool(item.get("ignore_weather", False)),
                "profile_id": str(item.get("profile_id") or "standard"),
                "active": False,
                "cancel": None,
                "end_ts": None,
            }

        self._programs.clear()
        programs_opt = self._options.get("programs", []) or []
        for idx, p in enumerate(programs_opt, start=1):
            try:
                pid = int(p.get("id", idx))
            except (TypeError, ValueError):
                pid = idx

            enabled = bool(p.get("enabled", False))
            name = p.get("name", f"Programma {pid}")

            time_str = p.get("time", "") or ""
            if len(time_str) == 5 and ":" in time_str:
                hh, mm = time_str.split(":")
            else:
                hh, mm = "08", "00"
            try:
                hh_i = max(0, min(23, int(hh)))
                mm_i = max(0, min(59, int(mm)))
            except (TypeError, ValueError):
                hh_i, mm_i = 8, 0
            time_norm = f"{hh_i:02d}:{mm_i:02d}"

            weekdays_set: Set[int] = set()
            days_val = p.get("days", "")
            if isinstance(days_val, str):
                tokens = [d.strip() for d in days_val.split(",") if d.strip()]
            else:
                tokens = list(days_val or [])
            for code in tokens:
                idx_day = WEEKDAY_MAP.get(code.lower())
                if idx_day is not None:
                    weekdays_set.add(idx_day)

            zones_list: List[int] = []
            zones_val = p.get("zones", "")
            if isinstance(zones_val, str):
                z_tokens = [z.strip() for z in zones_val.split(",") if z.strip()]
            else:
                z_tokens = [str(z) for z in (zones_val or [])]
            for token in z_tokens:
                try:
                    zid = int(token)
                except (TypeError, ValueError):
                    continue
                if zid in self._zones:
                    zones_list.append(zid)

            try:
                pause_minutes = float(p.get("pause_minutes", 0) or 0)
            except (TypeError, ValueError):
                pause_minutes = 0.0

            zone_overrides_raw = p.get("zone_durations", {}) or {}
            zone_overrides: Dict[int, float] = {}
            if isinstance(zone_overrides_raw, dict):
                for zk, zv in zone_overrides_raw.items():
                    try:
                        zid = int(zk)
                        mv = float(zv)
                        if mv > 0:
                            zone_overrides[zid] = mv
                    except (TypeError, ValueError):
                        continue

            self._programs.append(
                {
                    "id": pid,
                    "enabled": enabled,
                    "name": name,
                    "time": time_norm,
                    "days": tokens, # Store original days tokens for persistence
                    "weekdays": weekdays_set,
                    "zones": zones_list,
                    "pause_minutes": pause_minutes,
                    "zone_durations": zone_overrides,
                }
            )

    @staticmethod
    def _normalize_esunmind_weather_url(url: Any) -> str:
        value = str(url or "").strip()
        if value.endswith("/api/data"):
            return value[: -len("/api/data")] + "/api/weather/irrigation"
        return value

    @staticmethod
    def _to_float_or_none(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            text = str(value).strip().lower()
            if text in ("", "unknown", "unavailable", "none", "nan"):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _normalize_custom_profiles(self, profiles: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(profiles, list):
            return out
        for item in profiles:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or item.get("name") or "").strip().lower()
            profile_id = "".join(ch if ch.isalnum() else "_" for ch in raw_id).strip("_")
            if not profile_id or profile_id in BUILTIN_ZONE_PROFILES:
                continue
            name = str(item.get("name") or profile_id).strip()
            try:
                mult = float(item.get("smart_multiplier", 1.0))
            except (TypeError, ValueError):
                mult = 1.0
            out.append({
                "id": profile_id,
                "name": name,
                "smart_multiplier": self._clamp(mult, 0.2, 2.5),
                "wind_sensitive": bool(item.get("wind_sensitive", True)),
            })
        return out

    def get_zone_profiles(self) -> List[Dict[str, Any]]:
        profiles = [dict(p) for p in BUILTIN_ZONE_PROFILES.values()]
        profiles.extend(dict(p) for p in self._custom_zone_profiles)
        return profiles

    def _zone_profile(self, zone: Dict[str, Any] | None) -> Dict[str, Any]:
        profile_id = str((zone or {}).get("profile_id") or "standard")
        for profile in self.get_zone_profiles():
            if str(profile.get("id")) == profile_id:
                return profile
        return dict(BUILTIN_ZONE_PROFILES["standard"])

    def _profile_smart_multiplier(self, zone: Dict[str, Any] | None) -> float:
        profile = self._zone_profile(zone)
        try:
            return self._clamp(float(profile.get("smart_multiplier", 1.0)), 0.2, 2.5)
        except (TypeError, ValueError):
            return 1.0

    async def _handle_weather_refresh_tick(self, now: datetime) -> None:
        await self.async_refresh_irrigation_weather()

    async def async_refresh_irrigation_weather(self) -> None:
        """Refresh normalized irrigation weather from e-SunMind in background."""
        url = self._esunmind_weather_api_url
        if not url:
            return
        try:
            session = async_get_clientsession(self._hass)
            async with session.get(url, timeout=8) as resp:
                if resp.status >= 400:
                    self._irrigation_weather_error = f"HTTP {resp.status}"
                    return
                payload = await resp.json(content_type=None)
                if isinstance(payload, dict):
                    self._irrigation_weather_cache = payload
                    self._irrigation_weather_cache_ts = time.time()
                    self._irrigation_weather_error = None
        except Exception as exc:
            self._irrigation_weather_error = str(exc)
            _LOGGER.debug("async_refresh_irrigation_weather failed: %s", exc)

    def _get_irrigation_weather_payload(self) -> Optional[Dict[str, Any]]:
        payload = self._irrigation_weather_cache
        if not isinstance(payload, dict) or not payload:
            return None
        cache_age = time.time() - float(self._irrigation_weather_cache_ts or 0.0)
        if cache_age > max(self._weather_max_age_seconds, 60.0):
            return None
        age = self._to_float_or_none(payload.get("age_seconds"))
        if age is not None and age > self._weather_max_age_seconds:
            return None
        if payload.get("available") is False:
            return None
        return payload

    def _get_legacy_weather_status_info(self) -> Tuple[bool, str]:
        """Legacy weather block based on configured HA sensors."""
        # 1. Check Rain
        if self._rain_sensor:
            state = self._hass.states.get(self._rain_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    # If binary sensor: on = rain = skip
                    if state.domain == "binary_sensor":
                        if state.state == "on":
                            return False, "Pioggia rilevata (sensore attivo)"
                    else:
                        # Numeric sensor (mm)
                        val = float(state.state)
                        if val > self._rain_threshold:
                            return False, f"Pioggia {val:.1f}mm > {self._rain_threshold}mm"
                except ValueError:
                    pass

        # 2. Check Temperature (Freeze protection)
        if self._temp_sensor:
            state = self._hass.states.get(self._temp_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    val = float(state.state)
                    if val < self._min_temp:
                        return False, f"Temp {val:.1f}°C < {self._min_temp}°C"
                except ValueError:
                    pass

        # 3. Check Wind
        if self._wind_sensor:
            state = self._hass.states.get(self._wind_sensor)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    val = float(state.state)
                    unit = str((state.attributes or {}).get("unit_of_measurement") or "").strip().lower()
                    if unit in ("m/s", "mps", "ms"):
                        val = val * 3.6
                    if val > self._wind_threshold:
                        return False, f"Vento {val:.1f}km/h > {self._wind_threshold}km/h"
                except ValueError:
                    pass

        return True, "Condizioni Ottimali"

    def _get_professional_weather_status_info(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        reasons: list[str] = []
        source = payload.get("source") or "e-SunMind"
        age = self._to_float_or_none(payload.get("age_seconds"))
        if age is not None:
            reasons.append(f"{source}, dato {age:.0f}s")
        else:
            reasons.append(str(source))

        if bool(payload.get("freeze_block")):
            return False, "Blocco gelo da e-SunMind"
        if bool(payload.get("rain_block")) or bool(payload.get("is_raining")):
            return False, "Blocco pioggia da e-SunMind"
        if bool(payload.get("wind_block")):
            return False, "Blocco vento da e-SunMind"

        recent_rain = self._to_float_or_none(payload.get("rain_last_24h_mm"))
        if recent_rain is not None and recent_rain >= self._recent_rain_skip_mm:
            return False, f"Pioggia ultime 24h {recent_rain:.1f}mm >= {self._recent_rain_skip_mm:.1f}mm"

        forecast_rain = self._to_float_or_none(payload.get("forecast_rain_24h_mm"))
        if forecast_rain is not None and forecast_rain >= self._forecast_rain_skip_mm:
            return False, f"Pioggia prevista 24h {forecast_rain:.1f}mm >= {self._forecast_rain_skip_mm:.1f}mm"

        score = self._to_float_or_none(payload.get("irrigation_weather_score"))
        if score is not None:
            reasons.append(f"score {score:.0f}")
        reason = payload.get("irrigation_weather_reason")
        if reason:
            reasons.append(str(reason))
        return True, "OK meteo professionale: " + ", ".join(reasons)

    async def start_zone(self, zone_id: int, source: str = "manual") -> None:
        minutes = self.get_zone_duration(zone_id)
        # If the zone has no configured duration, set a sensible default so
        # that externally toggling the relay doesn't leave it on forever.
        if minutes <= 0:
            _LOGGER.debug(
                "start_zone: zone %s has no duration configured, setting default %s minutes",
                zone_id,
                DEFAULT_ZONE_MINUTES,
            )
            # set_zone_duration will notify listeners via dispatcher
            self.set_zone_duration(zone_id, DEFAULT_ZONE_MINUTES)
            minutes = DEFAULT_ZONE_MINUTES

        await self.start_zone_for(zone_id, minutes, source=source)

    async def start_zone_for(self, zone_id: int, minutes: float, source: str = "manual") -> None:
        zone = self._zones.get(zone_id)
        if not zone:
            return

        # Ensure no overlap between zones: turn off other active zones first,
        # then activate the requested one. When running as part of a program
        # the program may hold the master lock so the master valve remains
        # on for the entire sequence (including pauses).
        for other in list(self._zones.values()):
            if other["id"] != zone_id and other.get("active"):
                try:
                    await self._turn_off_zone(other["id"])
                except Exception:
                    _LOGGER.exception("start_zone_for: failed to turn off other zone %s", other.get("id"))

        await self._turn_on_zone(zone_id, minutes, source=source)

    async def stop_zone(self, zone_id: int) -> None:
        await self._turn_off_zone(zone_id)

    async def _update_master_switch(self) -> None:
        """Turn master switch on if any zone is active, else off."""
        if not self._master_switch_entity_id:
            return

        # If a program has locked the master, don't change it here.
        if getattr(self, "_master_lock", False):
            _LOGGER.debug("_update_master_switch: master locked by program, skipping update")
            return

        any_active = any(z.get("active") for z in self._zones.values())
        service = "turn_on" if any_active else "turn_off"
        
        try:
            await self._hass.services.async_call(
                "switch",
                service,
                {"entity_id": self._master_switch_entity_id},
                blocking=False,
            )
            _LOGGER.debug("_update_master_switch: %s %s", service, self._master_switch_entity_id)
        except Exception:
            _LOGGER.exception("Failed to update master switch %s", self._master_switch_entity_id)

    async def _program_master_on(self) -> None:
        """Explicitly turn the master switch on for a program run."""
        if not self._master_switch_entity_id:
            return
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": self._master_switch_entity_id},
                blocking=False,
            )
            _LOGGER.debug("_program_master_on: turned on master %s", self._master_switch_entity_id)
            try:
                self.log_event("master_on", f"Master {self._master_switch_entity_id} turned on for program", {})
            except Exception:
                _LOGGER.exception("_program_master_on: failed to log master_on")
        except Exception:
            _LOGGER.exception("_program_master_on: failed to call switch.turn_on for %s", self._master_switch_entity_id)

    async def _program_master_off(self, delay: float = MASTER_POST_SECONDS) -> None:
        """Turn the master switch off after an optional delay."""
        if not self._master_switch_entity_id:
            return
        try:
            if delay and delay > 0:
                await asyncio.sleep(delay)
            await self._hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": self._master_switch_entity_id},
                blocking=False,
            )
            _LOGGER.debug("_program_master_off: turned off master %s", self._master_switch_entity_id)
            try:
                self.log_event("master_off", f"Master {self._master_switch_entity_id} turned off after program", {})
            except Exception:
                _LOGGER.exception("_program_master_off: failed to log master_off")
        except Exception:
            _LOGGER.exception("_program_master_off: failed to call switch.turn_off for %s", self._master_switch_entity_id)

    async def _turn_on_zone(self, zone_id: int, minutes: float, source: str = "manual") -> None:
        zone = self._zones.get(zone_id)
        if not zone:
            return
        entity_id = zone.get("switch_entity_id")
        if not entity_id:
            return

        now = time.time()
        end_ts = now + minutes * 60.0

        if zone.get("cancel"):
            zone["cancel"]()
            zone["cancel"] = None

        async def _turn_off_later(_now):
            await self._turn_off_zone(zone_id)

        zone["cancel"] = async_call_later(
            self._hass, minutes * 60.0, _turn_off_later
        )
        zone["active"] = True
        zone["start_ts"] = now
        zone["end_ts"] = end_ts
        # Record source of this activation (manual/program/other)
        try:
            zone["last_start_source"] = source
        except Exception:
            zone["last_start_source"] = "unknown"

        # Update master switch
        await self._update_master_switch()

        _LOGGER.debug("_turn_on_zone: zone=%s entity=%s minutes=%s end_ts=%s", zone_id, entity_id, minutes, end_ts)
        await self._hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
            blocking=False,
        )
        _LOGGER.debug("_turn_on_zone: called switch.turn_on for %s", entity_id)

        # Log valve activation attempt (suppress during program-controlled master)
        try:
            if not getattr(self, "_master_lock", False):
                zname = zone.get("name")
                self.log_event("valve_activated", f"Activated valve {entity_id}", {"zone_name": zname, "entity_id": entity_id, "action": "turn_on", "source": source})
            else:
                _LOGGER.debug("_turn_on_zone: master_lock active, suppressing valve_activated log for %s", entity_id)
        except Exception:
            _LOGGER.exception("_turn_on_zone: failed to log valve_activated event")

        async_dispatcher_send(
            self._hass, f"{DOMAIN}_zone_updated_{self._entry.entry_id}", zone_id
        )
        _LOGGER.debug("_turn_on_zone: dispatched zone_updated for %s", zone_id)
        try:
            # Log event for addon consumption (include source)
            zname = zone.get("name")
            self.log_event("zone_start", f"Zone {zone_id} started", {"zone_name": zname, "minutes": minutes, "source": source})
        except Exception:
            _LOGGER.exception("_turn_on_zone: failed to log zone_start event")
        
        # Cancel any pending master-off timer because a zone is now active
        try:
            if hasattr(self, "_master_off_handle") and self._master_off_handle:
                try:
                    self._master_off_handle()
                except Exception:
                    pass
                self._master_off_handle = None
        except Exception:
            _LOGGER.exception("_turn_on_zone: failed to cancel master_off_handle")

        # Ensure master is ON when a zone becomes active (unless program locked it)
        try:
            if self._master_switch_entity_id and not getattr(self, "_master_lock", False):
                await self._hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": self._master_switch_entity_id},
                    blocking=False,
                )
        except Exception:
            _LOGGER.exception("_turn_on_zone: failed to ensure master switch is on")

    async def _turn_off_zone(self, zone_id: int) -> None:
        zone = self._zones.get(zone_id)
        if not zone:
            return
        entity_id = zone.get("switch_entity_id")
        if not entity_id:
            return

        if zone.get("cancel"):
            zone["cancel"]()
            zone["cancel"] = None

        # Calculate duration before clearing state
        start_ts = zone.get("start_ts")
        if start_ts:
            duration_sec = time.time() - start_ts
            if duration_sec > 1.0: # Ignore very short blips
                async_dispatcher_send(
                    self._hass, 
                    f"{DOMAIN}_irrigation_finished_{self._entry.entry_id}", 
                    duration_sec
                )

        zone["active"] = False
        zone["start_ts"] = None
        zone["end_ts"] = None

        # Update master switch
        await self._update_master_switch()

        await self._hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": entity_id},
            blocking=False,
        )
        _LOGGER.debug("_turn_off_zone: called switch.turn_off for %s", entity_id)

        # Log valve deactivation attempt (suppress during program-controlled master)
        try:
            src = zone.get("last_start_source")
            if not getattr(self, "_master_lock", False):
                zname = zone.get("name")
                self.log_event("valve_deactivated", f"Deactivated valve {entity_id}", {"zone_name": zname, "entity_id": entity_id, "action": "turn_off", "source": src})
            else:
                _LOGGER.debug("_turn_off_zone: master_lock active, suppressing valve_deactivated log for %s", entity_id)
        except Exception:
            _LOGGER.exception("_turn_off_zone: failed to log valve_deactivated event")

        # Schedule delayed master off to avoid brief toggles between zones.
        try:
            # Cancel existing scheduled off if present
            if hasattr(self, "_master_off_handle") and self._master_off_handle:
                try:
                    self._master_off_handle()
                except Exception:
                    pass
                self._master_off_handle = None

            # If a program holds the master, don't schedule an off here
            if getattr(self, "_master_lock", False):
                _LOGGER.debug("_turn_off_zone: master locked by program, not scheduling off")
            else:
                def _delayed_off(_now):
                    # If any zone became active, keep master on; otherwise turn off.
                    try:
                        any_active = any(z.get("active") for z in self._zones.values())
                        if any_active:
                            _LOGGER.debug("_delayed_off: found active zone, skipping master off")
                            return
                        # Call master off
                        try:
                            self._hass.async_create_task(self._hass.services.async_call(
                                "switch",
                                "turn_off",
                                {"entity_id": self._master_switch_entity_id},
                                blocking=False,
                            ))
                        except Exception:
                            _LOGGER.exception("_delayed_off: failed to call switch.turn_off")
                        try:
                            self.log_event("master_off", f"Master {self._master_switch_entity_id} turned off (delayed)", {})
                        except Exception:
                            _LOGGER.exception("_delayed_off: failed to log master_off")
                    except Exception:
                        _LOGGER.exception("_delayed_off: unexpected error")

                # Schedule delay
                self._master_off_handle = async_call_later(self._hass, MASTER_POST_SECONDS, _delayed_off)
        except Exception:
            _LOGGER.exception("_turn_off_zone: failed to schedule delayed master off")

        async_dispatcher_send(
            self._hass, f"{DOMAIN}_zone_updated_{self._entry.entry_id}", zone_id
        )
        _LOGGER.debug("_turn_off_zone: dispatched zone_updated for %s", zone_id)
        try:
            src = zone.get("last_start_source")
            zname = zone.get("name")
            self.log_event("zone_stop", f"Zone {zone_id} stopped", {"zone_name": zname, "source": src})
        except Exception:
            _LOGGER.exception("_turn_off_zone: failed to log zone_stop event")

    async def stop_all_zones(self) -> None:
        """Stop all active zones immediately."""
        for z in list(self._zones.values()):
            try:
                if z.get("active"):
                    await self._turn_off_zone(z["id"])
            except Exception:
                _LOGGER.exception("stop_all_zones: error stopping zone %s", z.get("id"))

    async def cancel_current_program(self) -> None:
        """Cancel the currently running program task, if any."""
        if self._program_task and not self._program_task.done():
            _LOGGER.debug("cancel_current_program: cancelling current program task")
            self._program_task.cancel()
            try:
                await self._program_task
            except asyncio.CancelledError:
                _LOGGER.debug("cancel_current_program: program task cancelled")
            finally:
                self._program_task = None
        # clear current program id reference
        self._current_program_id = None

    async def stop_programs(self) -> None:
        """Stop current program and all zones (stop program execution).

        This is called by the STOP button: it cancels a running program and
        turns off any active zones so that subsequent program steps don't run.
        """
        _LOGGER.debug("stop_programs: user requested STOP, cancelling program and stopping zones")
        await self.cancel_current_program()
        await self.stop_all_zones()

    def set_programs_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduled programs.

        If disabling, cancel any running program and stop zones.
        """
        self._programs_enabled = bool(enabled)
        _LOGGER.debug("set_programs_enabled: %s", self._programs_enabled)
        # Notify listeners (UI entities) that the flag changed
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_programs_updated_{self._entry.entry_id}", self._programs_enabled
        )
        # If being disabled, cancel running programs and stop zones.
        if not self._programs_enabled:
            try:
                self._hass.async_create_task(self.stop_programs())
            except Exception:
                _LOGGER.exception("set_programs_enabled: failed to schedule stop_programs task")

    def programs_enabled(self) -> bool:
        return bool(self._programs_enabled)

    def _serialize_programs(self) -> List[Dict[str, Any]]:
        """Serialize programs for config entry storage."""
        out = []
        for p in self._programs:
            d = dict(p)
            # Remove runtime-only fields
            d.pop("weekdays", None)
            # Ensure zones is list of ints (or strings if preferred, but ints are safer for logic)
            # options_flow handles strings or ints. Let's store as ints to be clean.
            if "zones" in d:
                d["zones"] = list(d["zones"])
            out.append(d)
        return out

    async def update_program(self, data: Dict[str, Any]) -> None:
        """Update, create or delete a program from service call."""
        program_id = int(data.get("program_id", 0))
        delete = bool(data.get("delete_program", False))

        if delete:
            # Remove program
            original_len = len(self._programs)
            self._programs = [p for p in self._programs if int(p.get("id", 0)) != program_id]
            if len(self._programs) == original_len:
                _LOGGER.warning("update_program: requested delete for non-existent program %s", program_id)
                return
            _LOGGER.info("update_program: deleted program %s", program_id)
            try:
                self.log_event("program_deleted", f"Program {program_id} deleted", {"program_id": program_id})
            except Exception:
                _LOGGER.exception("update_program: failed to log program_deleted event")
        else:
            # Create or Update
            target_prog = None
            for p in self._programs:
                if int(p.get("id", 0)) == program_id:
                    target_prog = p
                    break
            
            created = False
            if not target_prog:
                # Create new
                if program_id == 0:
                    program_id = (max([int(p.get("id", 0) or 0) for p in self._programs]) + 1) if self._programs else 1
                
                target_prog = {
                    "id": program_id,
                    "enabled": True,
                    "name": f"Programma {program_id}",
                    "time": "08:00",
                    "days": [],
                    "weekdays": set(),
                    "zones": [],
                    "pause_minutes": 0.0,
                }
                self._programs.append(target_prog)
                _LOGGER.info("update_program: created new program %s", program_id)
                created = True

            # Update fields if provided
            if "name" in data:
                target_prog["name"] = str(data["name"])
            if "enabled" in data:
                target_prog["enabled"] = bool(data["enabled"])
            if "time" in data:
                target_prog["time"] = str(data["time"])
            if "pause_minutes" in data:
                target_prog["pause_minutes"] = float(data["pause_minutes"])
            
            if "days" in data:
                days_val = data["days"]
                days_list = []
                if isinstance(days_val, list):
                    days_list = days_val
                elif isinstance(days_val, str):
                    days_list = [d.strip() for d in days_val.split(",") if d.strip()]
                
                target_prog["days"] = days_list
                
                # Update runtime weekdays set
                weekdays_set = set()
                for code in days_list:
                    idx = WEEKDAY_MAP.get(code.lower())
                    if idx is not None:
                        weekdays_set.add(idx)
                target_prog["weekdays"] = weekdays_set

            if "zones" in data:
                zones_val = data["zones"]
                new_zones = []
                if isinstance(zones_val, list):
                    # Can be list of ints (IDs) or list of strings (entity_ids or IDs)
                    registry = er.async_get(self._hass)
                    for z in zones_val:
                        # If it's an int, it's a zone ID
                        if isinstance(z, int):
                            new_zones.append(z)
                            continue
                        
                        z_str = str(z).strip()
                        # Try to parse as int first
                        try:
                            new_zones.append(int(z_str))
                            continue
                        except ValueError:
                            pass
                        
                        # If not int, assume entity_id. Try to resolve to zone ID.
                        # Check if it matches a zone switch created by this integration
                        ent_entry = registry.async_get(z_str)
                        if ent_entry and ent_entry.unique_id:
                            # Pattern: {entry_id}_zone_{zid}_switch
                            prefix = f"{self._entry.entry_id}_zone_"
                            suffix = "_switch"
                            if ent_entry.unique_id.startswith(prefix) and ent_entry.unique_id.endswith(suffix):
                                try:
                                    zid_str = ent_entry.unique_id[len(prefix):-len(suffix)]
                                    new_zones.append(int(zid_str))
                                    continue
                                except ValueError:
                                    pass
                        
                        # Fallback: check if it matches the physical switch_entity_id of any zone
                        found_phys = False
                        for zone in self._zones.values():
                            if zone.get("switch_entity_id") == z_str:
                                new_zones.append(int(zone["id"]))
                                found_phys = True
                                break
                        if found_phys:
                            continue

                elif isinstance(zones_val, str):
                    for z in zones_val.split(","):
                        if z.strip():
                            try: new_zones.append(int(z.strip()))
                            except: pass
                
                # Remove duplicates and sort
                target_prog["zones"] = sorted(list(set(new_zones)))

        # Persist changes
        self._options["programs"] = self._serialize_programs()
        try:
            self._hass.config_entries.async_update_entry(self._entry, options=self._options)
        except Exception:
            _LOGGER.exception("update_program: failed to persist options")

        # Notify listeners
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_programs_updated_{self._entry.entry_id}", True
        )
        if not delete:
             async_dispatcher_send(
                self._hass, f"{DOMAIN}_program_updated_{self._entry.entry_id}_{program_id}", program_id
            )
        # Log program created/updated event
        try:
            if delete:
                # already logged above
                pass
            else:
                if created:
                    self.log_event("program_created", f"Program {program_id} created", {"program_id": program_id})
                else:
                    # report which fields were in the request for context
                    applied = {k: data.get(k) for k in ("name", "enabled", "time", "pause_minutes", "days", "zones") if k in data}
                    self.log_event("program_updated", f"Program {program_id} updated", {"program_id": program_id, "changes": applied})
        except Exception:
            _LOGGER.exception("update_program: failed to log program event")

    async def update_zone(self, data: Dict[str, Any]) -> None:
        """Update zone name or duration from service call."""
        zone_input = data.get("zone_id")
        if not zone_input:
            return

        # Resolve zone ID
        zid = None
        if isinstance(zone_input, int):
            zid = zone_input
        else:
            z_str = str(zone_input).strip()
            # Try parsing int
            try:
                zid = int(z_str)
            except ValueError:
                pass
            
            if zid is None:
                # Try resolving entity_id
                registry = er.async_get(self._hass)
                ent_entry = registry.async_get(z_str)
                if ent_entry and ent_entry.unique_id:
                    prefix = f"{self._entry.entry_id}_zone_"
                    suffix = "_switch"
                    if ent_entry.unique_id.startswith(prefix) and ent_entry.unique_id.endswith(suffix):
                        try:
                            zid = int(ent_entry.unique_id[len(prefix):-len(suffix)])
                        except ValueError:
                            pass
            
            if zid is None:
                # Fallback: check physical switch
                for zone in self._zones.values():
                    if zone.get("switch_entity_id") == z_str:
                        zid = int(zone["id"])
                        break

        if zid is None or zid not in self._zones:
            _LOGGER.warning("update_zone: could not resolve zone %s", zone_input)
            return

        zone = self._zones[zid]
        old_zone_name = zone.get("name")
        changed = False

        if "name" in data:
            new_name = str(data["name"]).strip()
            if new_name:
                zone["name"] = new_name
                changed = True
        
        if "base_minutes" in data:
            try:
                new_dur = float(data["base_minutes"])
                if new_dur > 0:
                    zone["base_minutes"] = new_dur
                    changed = True
            except (TypeError, ValueError):
                pass

        if "ignore_weather" in data:
            try:
                new_val = bool(data.get("ignore_weather"))
            except Exception:
                new_val = False
            if zone.get("ignore_weather") != new_val:
                zone["ignore_weather"] = new_val
                changed = True

        if "profile_id" in data:
            profile_id = str(data.get("profile_id") or "standard").strip()
            valid_profile_ids = {str(p.get("id")) for p in self.get_zone_profiles()}
            if profile_id in valid_profile_ids and zone.get("profile_id") != profile_id:
                zone["profile_id"] = profile_id
                changed = True

        if changed:
            # Update options list
            zones_list = []
            for z in self._zones.values():
                zones_list.append({
                    "id": z["id"],
                    "name": z["name"],
                    "switch_entity_id": z.get("switch_entity_id"),
                    "base_minutes": z.get("base_minutes", 10.0),
                    "ignore_weather": bool(z.get("ignore_weather", False)),
                    "profile_id": str(z.get("profile_id") or "standard"),
                })
            self._options["zones"] = zones_list
            
            try:
                self._hass.config_entries.async_update_entry(self._entry, options=self._options)
            except Exception:
                _LOGGER.exception("update_zone: failed to persist options")

            # Notify listeners
            async_dispatcher_send(
                self._hass, f"{DOMAIN}_zone_updated_{self._entry.entry_id}", zid
            )

            try:
                applied = {k: data.get(k) for k in ("name", "base_minutes", "ignore_weather", "profile_id") if k in data}
                zname = zone.get("name")
                self.log_event("zone_update", f"Zone {zid} updated", {"zone_name": zname, "changes": applied})
            except Exception:
                _LOGGER.exception("update_zone: failed to log zone_update event")
            # Update Entity Registry names
            registry = er.async_get(self._hass)
            entries = er.async_entries_for_config_entry(registry, self._entry.entry_id)
            
            base_unique_id = f"{self._entry.entry_id}_zone_{zid}"
            
            # Map suffix to name generator
            suffix_map = {
                "_switch": lambda n: n,
                "_progress": lambda n: f"{n} - progresso",
                "_configured_duration": lambda n: f"{n} - durata configurata",
                "_remaining": lambda n: f"{n} - remaining",
                "_ignore_weather": lambda n: f"{n} - ignora meteo",
            }

            for entity_entry in entries:
                if entity_entry.unique_id.startswith(base_unique_id):
                    current_suffix = entity_entry.unique_id[len(base_unique_id):]
                    if current_suffix in suffix_map:
                        new_entity_name = suffix_map[current_suffix](zone["name"])
                        _LOGGER.debug("update_zone: renaming entity %s to %s", entity_entry.entity_id, new_entity_name)
                        # Update original_name so HA reflects the change (unless user overrode it)
                        registry.async_update_entity(entity_entry.entity_id, original_name=new_entity_name)

            # Update Physical Switch Name (Best Effort)
            # This renames the underlying relay entity (e.g. switch.relay_1) to "Relay 1 - ZoneName"
            phys_id = zone.get("switch_entity_id")
            if phys_id and "name" in data:
                phys_entry = registry.async_get(phys_id)
                if phys_entry:
                    # Use 'name' (friendly name override) or fall back to original_name or entity_id
                    current_name = phys_entry.name or phys_entry.original_name or phys_id
                    new_zone_name = zone["name"]
                    
                    suffix_old = f" - {old_zone_name}" if old_zone_name else ""
                    suffix_new = f" - {new_zone_name}"
                    
                    new_phys_name = current_name
                    
                    # If the current name ends with " - OldZoneName", replace it
                    if suffix_old and current_name.endswith(suffix_old):
                        new_phys_name = current_name[:-len(suffix_old)] + suffix_new
                    else:
                        # Otherwise, append the new suffix if it's not already there
                        if not current_name.endswith(suffix_new):
                             new_phys_name = f"{current_name}{suffix_new}"
                    
                    if new_phys_name != current_name:
                        _LOGGER.debug("update_zone: renaming physical relay %s to %s", phys_id, new_phys_name)
                        # We update 'name' (friendly name) because we don't own this entity
                        registry.async_update_entity(phys_id, name=new_phys_name)

            # Reload entry to update entity names in registry if needed
            self._hass.async_create_task(self._hass.config_entries.async_reload(self._entry.entry_id))

    async def set_program_enabled(self, program_id: int, enabled: bool) -> None:
        """Enable or disable a single program and persist the options.

        This updates the in-memory programs list, persists the options to the
        config entry (so the integration state is saved), and notifies
        listeners. If disabling the currently running program, it will be
        cancelled and active zones stopped.
        """
        found = False
        for p in self._programs:
            try:
                if int(p.get("id", 0) or 0) == int(program_id):
                    p["enabled"] = bool(enabled)
                    found = True
                    break
            except Exception:
                continue

        if not found:
            _LOGGER.debug("set_programs_enabled: program %s not found", program_id)
            return

        # persist options
        self._options["programs"] = list(self._programs)
        try:
            # async_update_entry is synchronous; call without await
            self._hass.config_entries.async_update_entry(self._entry, options=self._options)
        except Exception:
            _LOGGER.exception("set_programs_enabled: failed to persist options for program %s", program_id)

        # notify program-specific listeners and generic ones
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_program_updated_{self._entry.entry_id}_{program_id}", program_id
        )
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_programs_updated_{self._entry.entry_id}", True
        )

        try:
            ev_type = "program_enabled" if enabled else "program_disabled"
            self.log_event(ev_type, f"Program {program_id} {'enabled' if enabled else 'disabled'}", {"program_id": program_id})
        except Exception:
            _LOGGER.exception("set_programs_enabled: failed to log program enable/disable event")

        # If disabling the program that's currently running, cancel it
        try:
            if not enabled and self._current_program_id == int(program_id):
                _LOGGER.debug("set_programs_enabled: disabling currently running program %s, cancelling", program_id)
                await self.cancel_current_program()
                await self.stop_all_zones()
        except Exception:
            _LOGGER.exception("set_programs_enabled: error handling running program for %s", program_id)

    async def update_weather_settings(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update weather and SmartCalc options from service/add-on UI."""
        allowed = {
            "rain_sensor_entity_id",
            "rain_threshold",
            "temp_sensor_entity_id",
            "min_temp",
            "humidity_sensor_entity_id",
            "wind_sensor_entity_id",
            "wind_threshold",
            "enable_smart_calc",
            "esunmind_weather_api_url",
            "weather_max_age_seconds",
            "forecast_rain_skip_mm",
            "recent_rain_skip_mm",
        }
        numeric_keys = {
            "rain_threshold",
            "min_temp",
            "wind_threshold",
            "weather_max_age_seconds",
            "forecast_rain_skip_mm",
            "recent_rain_skip_mm",
        }
        applied: Dict[str, Any] = {}
        for key in allowed:
            if key not in data:
                continue
            value = data.get(key)
            if key == "enable_smart_calc":
                value = bool(value)
            elif key in numeric_keys:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            elif value is not None:
                value = str(value).strip()
            self._options[key] = value
            applied[key] = value

        if not applied:
            return {}

        try:
            self._hass.config_entries.async_update_entry(self._entry, options=self._options)
        except Exception:
            _LOGGER.exception("update_weather_settings: failed to persist options")

        self.reload_options(self._options)
        await self.async_refresh_irrigation_weather()
        try:
            self.log_event("weather_settings", "Tarature meteo aggiornate", applied)
        except Exception:
            _LOGGER.debug("update_weather_settings: failed to log event")
        async_dispatcher_send(self._hass, f"{DOMAIN}_weather_updated_{self._entry.entry_id}", applied)
        return applied

    async def update_zone_profiles(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Persist custom zone profiles for SmartCalc per-zone behavior."""
        profiles = self._normalize_custom_profiles(data.get("profiles") or [])
        self._options["custom_zone_profiles"] = profiles
        try:
            self._hass.config_entries.async_update_entry(self._entry, options=self._options)
        except Exception:
            _LOGGER.exception("update_zone_profiles: failed to persist options")

        self.reload_options(self._options)
        try:
            self.log_event(
                "zone_profiles_update",
                "Preset zone aggiornati",
                {"custom_profiles": profiles},
            )
        except Exception:
            _LOGGER.debug("update_zone_profiles: failed to log event")
        async_dispatcher_send(self._hass, f"{DOMAIN}_zone_updated_{self._entry.entry_id}", True)
        return profiles

    async def stop_program(self, program_id: int) -> None:
        """Stop (cancel) a single program if it's running.

        If the program is currently running, cancel its task and stop all
        active zones. Otherwise do nothing.
        """
        try:
            if self._current_program_id == int(program_id):
                _LOGGER.debug("stop_program: cancelling running program %s", program_id)
                await self.cancel_current_program()
                await self.stop_all_zones()
            else:
                _LOGGER.debug("stop_program: program %s is not running, nothing to do", program_id)
        except Exception:
            _LOGGER.exception("stop_program: error while stopping program %s", program_id)

    def get_next_scheduled_run(self) -> datetime | None:
        """Calculate the next scheduled run time across all enabled programs."""
        if not self._programs_enabled:
            return None

        now = as_local(datetime.now())
        next_run: datetime | None = None

        for prog in self._programs:
            if not prog.get("enabled"):
                continue
            
            weekdays = prog.get("weekdays", set())
            if not weekdays:
                continue
                
            time_str = prog.get("time", "08:00")
            try:
                hh, mm = map(int, time_str.split(":"))
            except ValueError:
                continue

            # Check next 8 days (today + 7 days) to cover full week cycle
            for d in range(8):
                check_date = now.date() + timedelta(days=d)
                candidate = datetime(
                    check_date.year, check_date.month, check_date.day,
                    hh, mm, 0, 0, tzinfo=now.tzinfo
                )
                
                if candidate <= now:
                    continue
                
                if check_date.weekday() in weekdays:
                    if next_run is None or candidate < next_run:
                        next_run = candidate
                    break # Found next occurrence for this program
        
        return next_run

    async def _handle_time_tick(self, now: datetime) -> None:
        local_now = as_local(now)
        weekday_idx = local_now.weekday()
        hh = local_now.hour
        mm = local_now.minute
        current_hhmm = f"{hh:02d}:{mm:02d}"

        if not self._programs_enabled:
            _LOGGER.debug("_handle_time_tick: programs disabled, skipping scheduled runs")
            return

        for prog in self._programs:
            if not prog.get("enabled"):
                continue
            if weekday_idx not in prog.get("weekdays", set()):
                continue
            if current_hhmm != prog.get("time"):
                continue

            # Launch program execution as a cancellable task so we can stop it
            # from outside (STOP button or disabling programs).
            if self._program_task is None or self._program_task.done():
                self._program_task = self._hass.async_create_task(self._run_program(prog))
                try:
                    self._current_program_id = int(prog.get("id") or 0)
                except Exception:
                    self._current_program_id = None
            else:
                _LOGGER.debug("_handle_time_tick: a program task is already running, skipping start")

    def get_weather_status_info(self) -> Tuple[bool, str]:
        """Check weather and return status + reason."""
        payload = self._get_irrigation_weather_payload()
        if payload:
            return self._get_professional_weather_status_info(payload)
        return self._get_legacy_weather_status_info()

    def get_smart_calc_info(self) -> Tuple[float, str]:
        """Calculate smart adjustment and return factor + explanation."""
        if not self._enable_smart_calc:
            return 1.0, "Smart Calc disabilitato"

        payload = self._get_irrigation_weather_payload()
        if payload:
            temp = self._to_float_or_none(payload.get("temperature_c"))
            hum = self._to_float_or_none(payload.get("humidity_pct"))
            et0 = self._to_float_or_none(payload.get("et0_mm_day"))
            rain_24h = self._to_float_or_none(payload.get("rain_last_24h_mm"))
            forecast_rain = self._to_float_or_none(payload.get("forecast_rain_24h_mm"))
            solar = self._to_float_or_none(payload.get("solar_radiation_w_m2"))
            wind_ms = self._to_float_or_none(payload.get("wind_speed_ms"))
            score = self._to_float_or_none(payload.get("irrigation_weather_score"))

            factor = 1.0
            parts: list[str] = ["Base 1.00"]

            if et0 is not None:
                delta = self._clamp((et0 - 3.5) * 0.12, -0.25, 0.35)
                factor *= 1.0 + delta
                parts.append(f"ET0 {et0:.1f}mm/g ({delta:+.2f})")
            if rain_24h is not None and rain_24h > 0:
                mult = self._clamp(1.0 - (rain_24h / 10.0), 0.20, 1.0)
                factor *= mult
                parts.append(f"Pioggia 24h {rain_24h:.1f}mm (x{mult:.2f})")
            if forecast_rain is not None and forecast_rain > 0:
                mult = self._clamp(1.0 - (forecast_rain / 8.0), 0.0, 1.0)
                factor *= mult
                parts.append(f"Pioggia prevista {forecast_rain:.1f}mm (x{mult:.2f})")
            if temp is not None:
                delta = self._clamp((temp - 25.0) * 0.025, -0.20, 0.25)
                factor *= 1.0 + delta
                parts.append(f"Temp {temp:.1f}°C ({delta:+.2f})")
            if hum is not None:
                delta = self._clamp((50.0 - hum) * 0.01, -0.20, 0.25)
                factor *= 1.0 + delta
                parts.append(f"Hum {hum:.1f}% ({delta:+.2f})")
            if solar is not None:
                delta = 0.12 if solar >= 900 else 0.08 if solar >= 700 else 0.0
                if delta:
                    factor *= 1.0 + delta
                    parts.append(f"Sole {solar:.0f}W/m² ({delta:+.2f})")
            if wind_ms is not None and wind_ms > 0:
                wind_kmh = wind_ms * 3.6
                if wind_kmh >= self._wind_threshold * 0.75:
                    factor *= 0.90
                    parts.append(f"Vento {wind_kmh:.1f}km/h (x0.90)")
            if score is not None:
                mult = self._clamp(0.50 + (score / 200.0), 0.50, 1.00)
                factor *= mult
                parts.append(f"Score {score:.0f} (x{mult:.2f})")

            factor = self._clamp(factor, 0.0, 2.5)
            return factor, " + ".join(parts)
            
        temp = 20.0
        hum = 60.0
        
        if self._temp_sensor:
            st = self._hass.states.get(self._temp_sensor)
            if st and st.state not in ("unknown", "unavailable"):
                try: temp = float(st.state)
                except: pass
        
        if self._hum_sensor:
            sh = self._hass.states.get(self._hum_sensor)
            if sh and sh.state not in ("unknown", "unavailable"):
                try: hum = float(sh.state)
                except: pass
        
        # Logic:
        # Base is 1.0 at 20°C and 60% Humidity
        # +5% for every degree above 20
        # -1% for every % humidity above 60 (and vice versa)
        
        temp_diff = temp - 20.0
        hum_diff = hum - 60.0
        
        temp_factor = temp_diff * 0.05
        hum_factor = -(hum_diff * 0.01)
        
        factor = 1.0 + temp_factor + hum_factor
        
        # Clamp between 0.0 (skip) and 3.0 (300%)
        factor = max(0.0, min(3.0, factor))
        
        reason = f"Base 1.0 + Temp {temp:.1f}°C ({temp_factor:+.2f}) + Hum {hum:.1f}% ({hum_factor:+.2f})"
        return factor, reason

    def get_manual_adjustment(self) -> float:
        return self._manual_adjustment

    def set_manual_adjustment(self, value: float) -> None:
        self._manual_adjustment = float(value)
        _LOGGER.debug("Manual adjustment set to %.1f%%", self._manual_adjustment)
        # Notify listeners that global adjustment changed
        async_dispatcher_send(self._hass, f"{DOMAIN}_adjustment_updated_{self.entry_id}")

    def _get_adjustment_factor(self) -> float:
        """Get duration multiplier from manual adjustment and smart calc."""
        # Manual adjustment is a percentage (0-200), convert to factor
        manual_factor = self._manual_adjustment / 100.0
        
        smart_factor, _ = self.get_smart_calc_info()
        
        # Combine factors (multiply them)
        total_factor = manual_factor * smart_factor
        return total_factor

    async def _run_program(self, prog: Dict[str, Any]) -> None:
        zones: List[int] = prog.get("zones", []) or []
        if not zones:
            return

        pause = float(prog.get("pause_minutes", 0) or 0)
        zone_durations: Dict[int, float] = prog.get("zone_durations", {}) or {}
        
        # Check weather once at start of program
        weather_ok, weather_reason = self.get_weather_status_info()
        # Manual adjustment factor (percentage -> factor)
        manual_factor = self._manual_adjustment / 100.0
        # Smart factor (weather-based). We'll apply it per-zone unless the
        # zone has `ignore_weather` set, in which case smart_factor=1.0.
        smart_factor_global, smart_reason = self.get_smart_calc_info()
        
        if not weather_ok:
            _LOGGER.info("Program %s: Weather conditions unfavorable: %s", prog.get("id"), weather_reason)
        # Log if any adjustment will be applied (manual != 100% or smart != 1.0)
        if manual_factor != 1.0 or smart_factor_global != 1.0:
            _LOGGER.info(
                "Program %s: Manual factor=%.2f Smart factor=%.2f (%s)",
                prog.get("id"),
                manual_factor,
                smart_factor_global,
                smart_reason,
            )

        # Calcolo durata totale prevista
        total_minutes = 0.0
        for zid in zones:
            zone = self._zones.get(zid)
            if not zone:
                continue
            
            # Skip logic for calculation (respect ignore_weather for skipping)
            if not weather_ok and not zone.get("ignore_weather"):
                continue

            override = zone_durations.get(zid)
            base_d = (
                float(override)
                if override is not None and float(override) > 0
                else self.get_zone_duration(zid)
            )

            # Determine per-zone factor: always apply manual adjustment;
            # apply smart/weather factor only if zone does NOT ignore weather.
            profile = self._zone_profile(zone)
            profile_multiplier = self._profile_smart_multiplier(zone)
            zone_smart = smart_factor_global * profile_multiplier if not zone.get("ignore_weather") else 1.0
            total_factor = manual_factor * zone_smart

            # Apply adjustment
            d = base_d * total_factor
            try:
                # Diagnostic event for debugging/calibration
                self.log_event(
                    "zone_calc",
                    f"Calc zone {zone.get('name')}",
                    {
                        "zone_name": zone.get("name"),
                        "base_minutes": base_d,
                        "manual_factor": manual_factor,
                        "smart_factor_applied": zone_smart,
                        "global_smart_factor": smart_factor_global,
                        "profile_id": profile.get("id"),
                        "profile_name": profile.get("name"),
                        "profile_smart_multiplier": profile_multiplier,
                        "effective_minutes": d,
                        "ignore_weather": bool(zone.get("ignore_weather")),
                    },
                )
            except Exception:
                _LOGGER.debug("_run_program: failed to log zone_calc for %s", zone.get("name"))
            
            if d > 0:
                total_minutes += d
                if pause > 0:
                    total_minutes += pause
        
        self._current_program_start_ts = time.time()
        self._current_program_duration = total_minutes * 60.0

        # Acquire master control for the duration of this program so that the
        # master valve remains on the whole time (including during pauses).
        self._master_lock = True
        try:
            await self._program_master_on()
        except Exception:
            _LOGGER.exception("_run_program: failed to ensure master on")

        try:
            for zid in zones:
                # If programs were disabled while the program was running, stop.
                if not self._programs_enabled:
                    _LOGGER.debug(
                        "_run_program: programs disabled, aborting program %s",
                        prog.get("id"),
                    )
                    return

                zone = self._zones.get(zid)
                if not zone:
                    continue

                # Weather Check Logic
                if not weather_ok and not zone.get("ignore_weather"):
                    _LOGGER.info("Skipping zone %s due to weather conditions: %s", zone["name"], weather_reason)
                    continue

                override = zone_durations.get(zid)
                base_duration = (
                    float(override)
                    if override is not None and float(override) > 0
                    else self.get_zone_duration(zid)
                )

                # Determine per-zone factor: always apply manual adjustment;
                # apply smart/weather factor only if zone does NOT ignore weather.
                profile_multiplier = self._profile_smart_multiplier(zone)
                zone_smart = smart_factor_global * profile_multiplier if not zone.get("ignore_weather") else 1.0
                total_factor = manual_factor * zone_smart

                # Apply Adjustment Factor
                duration = base_duration * total_factor
                
                if duration <= 0:
                    continue

                await self.start_zone_for(zid, duration, source="program")
                try:
                    await asyncio.sleep(duration * 60)
                except asyncio.CancelledError:
                    _LOGGER.debug("_run_program: sleep cancelled during zone %s", zid)
                    raise

                if pause > 0:
                    try:
                        await asyncio.sleep(pause * 60)
                    except asyncio.CancelledError:
                        _LOGGER.debug(
                            "_run_program: sleep cancelled during pause after zone %s",
                            zid,
                        )
                        raise
        except asyncio.CancelledError:
            _LOGGER.debug("_run_program: program %s cancelled", prog.get("id"))
            raise
        finally:
            # Clear program task reference and current program id when finished.
            try:
                if self._program_task and self._program_task.done():
                    self._program_task = None
            except Exception:
                self._program_task = None
            self._current_program_id = None
            self._current_program_start_ts = None
            self._current_program_duration = None

            # Release master control after a short post-delay so downstream
            # devices (or UI) can observe final valve_off events.
            try:
                await self._program_master_off()
            except Exception:
                _LOGGER.exception("_run_program: failed to turn master off after program")
            finally:
                self._master_lock = False

        return
