from __future__ import annotations
from typing import Any, Dict, List

import json
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv

from .debug import setup_debug_logger
from .const import DOMAIN

# Assicurati che il file logger sia inizializzato
_INTEGRATION_LOGGER = setup_debug_logger()
_LOGGER = logging.getLogger(__name__)


def _sanitize_options(value: Any) -> Any:
    """Ricorsivamente converte le opzioni in tipi serializzabili JSON."""
    if isinstance(value, dict):
        return {k: _sanitize_options(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_options(v) for v in value]
    if isinstance(value, set):
        try:
            seq = sorted(value)
        except Exception:
            seq = list(value)
        return [_sanitize_options(v) for v in seq]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return str(value)
    except Exception:
        return repr(value)


class EDry2OptionsFlow(config_entries.OptionsFlow):
    """Flusso di opzioni per configurare zone e programmi (versione stabile)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        # LA CORREZIONE è QUI: usa `{}` se config_entry.options è None.
        self._options: Dict[str, Any] = dict(config_entry.options or {}) 
        self._zones: List[Dict[str, Any]] = self._options.get("zones", []) or []
        self._programs: List[Dict[str, Any]] = self._options.get("programs", []) or []
        self._program_edit_index: int | None = None

    async def async_step_init(self, user_input: Dict[str, Any] | None = None):
        try:
            if user_input is not None:
                choice = user_input.get("choice")
                if choice == "zones":
                    return await self.async_step_zones()
                elif choice == "programs":
                    return await self.async_step_programs()
                return await self.async_step_general()

            schema = vol.Schema({
                vol.Required("choice"): vol.In({
                    "zones": "Configura zone",
                    "programs": "Configura programmi",
                    "general": "Impostazioni Generali (Pompa Master & Meteo)"
                })
            })
            return self.async_show_form(step_id="init", data_schema=schema)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.exception("async_step_init unexpected error: %s", exc)
            _INTEGRATION_LOGGER.exception("async_step_init unexpected error: %s", exc)
            return self.async_show_form(step_id="init", data_schema=vol.Schema({}), errors={"base": "unknown"})

    async def async_step_general(self, user_input: Dict[str, Any] | None = None):
        """Configura impostazioni generali come la pompa master e meteo."""
        if user_input is not None:
            self._options["master_switch_entity_id"] = user_input.get("master_switch_entity_id")
            self._options["rain_sensor_entity_id"] = user_input.get("rain_sensor_entity_id")
            self._options["rain_threshold"] = user_input.get("rain_threshold")
            self._options["temp_sensor_entity_id"] = user_input.get("temp_sensor_entity_id")
            self._options["min_temp"] = user_input.get("min_temp")
            self._options["humidity_sensor_entity_id"] = user_input.get("humidity_sensor_entity_id")
            self._options["wind_sensor_entity_id"] = user_input.get("wind_sensor_entity_id")
            self._options["wind_threshold"] = user_input.get("wind_threshold")
            self._options["enable_smart_calc"] = user_input.get("enable_smart_calc")
            self._options["esunmind_weather_api_url"] = user_input.get("esunmind_weather_api_url")
            self._options["weather_max_age_seconds"] = user_input.get("weather_max_age_seconds")
            self._options["forecast_rain_skip_mm"] = user_input.get("forecast_rain_skip_mm")
            self._options["recent_rain_skip_mm"] = user_input.get("recent_rain_skip_mm")
            return self.async_create_entry(title="", data=self._options)

        current_master = self._options.get("master_switch_entity_id", "")
        current_rain = self._options.get("rain_sensor_entity_id") or "sensor.e_sunmind_weather_precip_1h_mm"
        current_rain_threshold = self._options.get("rain_threshold", 0.0)
        current_temp = self._options.get("temp_sensor_entity_id") or "sensor.e_sunmind_weather_temp_c"
        current_min_temp = self._options.get("min_temp", 5.0)
        current_hum = self._options.get("humidity_sensor_entity_id") or "sensor.e_sunmind_weather_humidity_pct"
        current_wind = self._options.get("wind_sensor_entity_id") or "sensor.e_sunmind_weather_wind_ms"
        current_wind_threshold = self._options.get("wind_threshold", 20.0)
        current_smart = self._options.get("enable_smart_calc", False)
        current_api_url = self._options.get("esunmind_weather_api_url") or "http://192.168.3.24:1980/api/weather/irrigation"
        current_max_age = self._options.get("weather_max_age_seconds", 900.0)
        current_forecast_skip = self._options.get("forecast_rain_skip_mm", 6.0)
        current_recent_skip = self._options.get("recent_rain_skip_mm", 4.0)
        
        from homeassistant.helpers import selector
        
        schema = vol.Schema({
            vol.Optional("master_switch_entity_id", default=current_master): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch")
            ),
            vol.Optional("rain_sensor_entity_id", default=current_rain): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "binary_sensor", "input_boolean"])
            ),
            vol.Optional("rain_threshold", default=current_rain_threshold): vol.Coerce(float),
            vol.Optional("temp_sensor_entity_id", default=current_temp): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional("min_temp", default=current_min_temp): vol.Coerce(float),
            vol.Optional("humidity_sensor_entity_id", default=current_hum): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional("wind_sensor_entity_id", default=current_wind): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional("wind_threshold", default=current_wind_threshold): vol.Coerce(float),
            vol.Optional("enable_smart_calc", default=current_smart): bool,
            vol.Optional("esunmind_weather_api_url", default=current_api_url): str,
            vol.Optional("weather_max_age_seconds", default=current_max_age): vol.Coerce(float),
            vol.Optional("forecast_rain_skip_mm", default=current_forecast_skip): vol.Coerce(float),
            vol.Optional("recent_rain_skip_mm", default=current_recent_skip): vol.Coerce(float),
        })

        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_zones(self, user_input: Dict[str, Any] | None = None):
        """Mostra varie righe di zona; quelle vuote vengono ignorate."""
        try:
            existing = list(self._zones)
            default_count = max(len(existing), 5)

            def _is_zone_fields_submission(data: Dict[str, Any]) -> bool:
                if not data:
                    return False
                for k in data.keys():
                    if isinstance(k, str) and k.startswith("zone_") and k.endswith("_name"):
                        return True
                return False

            if user_input and _is_zone_fields_submission(user_input):
                try:
                    max_rows = int(user_input.get("num_zones") or default_count)
                except Exception:
                    max_rows = default_count

                new_zones: List[Dict[str, Any]] = []
                new_id = 1
                for i in range(1, max_rows + 1):
                    name = (user_input.get(f"zone_{i}_name") or "").strip()
                    switch = (user_input.get(f"zone_{i}_switch") or "").strip()
                    ignore_weather = bool(user_input.get(f"zone_{i}_ignore_weather", False))
                    if not name:
                        continue
                    # Manteniamo base_minutes, come nella tua logica
                    new_zones.append({
                        "id": new_id, 
                        "name": name, 
                        "switch_entity_id": switch, 
                        "base_minutes": 10.0,
                        "ignore_weather": ignore_weather
                    })
                    new_id += 1

                self._zones = new_zones
                self._options["zones"] = new_zones

                try:
                    sanitized = _sanitize_options(self._options)
                    json.dumps(sanitized)
                    self._options = sanitized
                    self.hass.config_entries.async_update_entry(self._entry, options=self._options)
                    self.hass.async_create_task(self.hass.config_entries.async_reload(self._entry.entry_id))
                except Exception as exc:
                    _LOGGER.exception("Failed to update options (zones): %s", exc)
                    _INTEGRATION_LOGGER.exception("Failed persisting zones: %s", exc)
                    schema = vol.Schema({vol.Required("num_zones", default=default_count): vol.All(vol.Coerce(int), vol.Range(min=1, max=32))}, extra=vol.ALLOW_EXTRA)
                    return self.async_show_form(step_id="zones", data_schema=schema, errors={"base": "unknown"})

                return self.async_create_entry(title="", data=self._options)

            if user_input and not _is_zone_fields_submission(user_input):
                try:
                    requested = int(user_input.get("num_zones") or default_count)
                except Exception:
                    requested = default_count
                max_rows = max(1, min(32, requested))
                fields: Dict[Any, Any] = {}
                for i in range(1, max_rows + 1):
                    z = existing[i - 1] if i - 1 < len(existing) else {}
                    fields[vol.Optional(f"zone_{i}_name", default=z.get("name", ""))] = str
                    fields[vol.Optional(f"zone_{i}_switch", default=z.get("switch_entity_id", ""))] = str
                    fields[vol.Optional(f"zone_{i}_ignore_weather", default=z.get("ignore_weather", False))] = bool
                fields[vol.Optional("num_zones", default=max_rows)] = vol.Coerce(int)
                schema = vol.Schema(fields, extra=vol.ALLOW_EXTRA)
                return self.async_show_form(step_id="zones", data_schema=schema)

            schema = vol.Schema({vol.Required("num_zones", default=default_count): vol.All(vol.Coerce(int), vol.Range(min=1, max=32))}, extra=vol.ALLOW_EXTRA)
            return self.async_show_form(step_id="zones", data_schema=schema)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.exception("async_step_zones: unexpected error: %s", exc)
            _INTEGRATION_LOGGER.exception("async_step_zones unexpected error: %s", exc)
            schema = vol.Schema({vol.Required("num_zones", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=32))}, extra=vol.ALLOW_EXTRA)
            return self.async_show_form(step_id="zones", data_schema=schema, errors={"base": "unknown"})

    async def async_step_programs(self, user_input: Dict[str, Any] | None = None):
        """Mostra l'elenco dei programmi per modifica o cancellazione."""
        if user_input is not None:
            selected = user_input.get("program_action")
            if selected == "add_new":
                self._program_edit_index = None  # Nuovo programma
                return await self.async_step_program_edit()
            
            # selected è tipo "prog_123"
            try:
                pid = int(selected.split("_")[1])
                # Troviamo l'indice nell'array
                for idx, p in enumerate(self._programs):
                    if int(p.get("id", 0)) == pid:
                        self._program_edit_index = idx
                        break
                return await self.async_step_program_edit()
            except Exception:
                pass

        options = {"add_new": "Aggiungi nuovo programma"}
        for p in self._programs:
            pid = p.get("id")
            name = p.get("name") or f"Programma {pid}"
            options[f"prog_{pid}"] = f"Modifica: {name}"

        schema = vol.Schema({
            vol.Required("program_action"): vol.In(options)
        })
        return self.async_show_form(step_id="programs", data_schema=schema)

    async def async_step_program_edit(self, user_input: Dict[str, Any] | None = None):
        """Aggiunge o modifica un programma."""

        DAYS = {
            "mon": "Lunedì", "tue": "Martedì", "wed": "Mercoledì", "thu": "Giovedì",
            "fri": "Venerdì", "sat": "Sabato", "sun": "Domenica",
        }
        zone_choices: Dict[str, str] = {str(z["id"]): z.get("name", f"Zona {z['id']}") for z in self._zones}

        # Recupera dati esistenti se in modifica
        prog = {}
        if self._program_edit_index is not None and 0 <= self._program_edit_index < len(self._programs):
            prog = self._programs[self._program_edit_index]

        # Impostazioni di default
        default_enabled = prog.get("enabled", True)
        default_name = prog.get("name", "")
        default_time = prog.get("time", "08:00")
        
        # Parsing days
        default_days = []
        raw_days = prog.get("days", [])
        # Se è stringa o lista
        if isinstance(raw_days, list):
            default_days = raw_days
        elif isinstance(raw_days, str):
            default_days = [d.strip() for d in raw_days.split(",") if d.strip()]
        # Filtra solo chiavi valide
        default_days = [d for d in default_days if d in DAYS]

        # Parsing zones
        default_zones = []
        raw_zones = prog.get("zones", [])
        if isinstance(raw_zones, list):
            default_zones = [str(z) for z in raw_zones]
        elif isinstance(raw_zones, str):
            default_zones = [z.strip() for z in raw_zones.split(",") if z.strip()]
        # Filtra solo zone esistenti
        default_zones = [z for z in default_zones if z in zone_choices]

        default_pause = float(prog.get("pause_minutes", 0.0))

        schema_dict = {
            vol.Required("enabled", default=default_enabled): bool,
            vol.Required("name", default=default_name): str,
            vol.Required("time", default=default_time): str,
            vol.Optional("days", default=default_days): cv.multi_select(DAYS),
            vol.Required("zones", default=default_zones): cv.multi_select(zone_choices) if zone_choices else vol.All(list, vol.Length(min=0)),
            vol.Optional("pause_minutes", default=default_pause): vol.Coerce(float),
        }

        # Se siamo in modifica, aggiungi opzione cancellazione
        if self._program_edit_index is not None:
            schema_dict[vol.Optional("delete_program", default=False)] = bool

        schema = vol.Schema(schema_dict, extra=vol.ALLOW_EXTRA)

        if user_input:
            try:
                # Gestione cancellazione
                if user_input.get("delete_program"):
                    if self._program_edit_index is not None:
                        self._programs.pop(self._program_edit_index)
                        self._options["programs"] = self._programs
                        
                        sanitized = _sanitize_options(self._options)
                        self._options = sanitized
                        self.hass.config_entries.async_update_entry(self._entry, options=self._options)
                        self.hass.async_create_task(self.hass.config_entries.async_reload(self._entry.entry_id))
                        return self.async_create_entry(title="", data=self._options)

                days_sel = user_input.get("days", [])
                # days_str = ",".join(days_sel) if isinstance(days_sel, list) else str(days_sel) if days_sel else ""

                zones_sel = user_input.get("zones", [])
                # zones_str = ",".join(zones_sel) if isinstance(zones_sel, list) else str(zones_sel) if zones_sel else ""

                # Se modifica, mantieni ID, altrimenti nuovo
                if self._program_edit_index is not None:
                    pid = prog.get("id")
                else:
                    pid = (max([int(p.get("id", 0) or 0) for p in self._programs]) + 1) if self._programs else 1

                new_prog_data = {
                    "id": pid,
                    "enabled": user_input.get("enabled", True),
                    "name": user_input.get("name", ""),
                    "time": user_input.get("time", "08:00"),
                    "days": days_sel, # Salviamo come lista direttamente
                    "zones": zones_sel, # Salviamo come lista direttamente
                    "pause_minutes": user_input.get("pause_minutes", 0),
                }

                if self._program_edit_index is not None:
                    self._programs[self._program_edit_index] = new_prog_data
                else:
                    self._programs.append(new_prog_data)
                
                self._options["programs"] = self._programs

                # Persistenza
                sanitized = _sanitize_options(self._options)
                json.dumps(sanitized)
                self._options = sanitized
                self.hass.config_entries.async_update_entry(self._entry, options=self._options)
                self.hass.async_create_task(self.hass.config_entries.async_reload(self._entry.entry_id))

                return self.async_create_entry(title="", data=self._options)

            except Exception as exc:
                _LOGGER.exception("async_step_program_edit: unexpected error: %s", exc)
                _INTEGRATION_LOGGER.exception("async_step_program_edit unexpected error: %s", exc)
                return self.async_show_form(step_id="program_edit", data_schema=schema, errors={"base": "unknown"})

        return self.async_show_form(step_id="program_edit", data_schema=schema)


def async_get_options_flow(config_entry: config_entries.ConfigEntry):
    """Restituisce il gestore del flusso di opzioni per questa integrazione."""
    _LOGGER.debug("async_get_options_flow called for entry %s", getattr(config_entry, "entry_id", "?"))
    _INTEGRATION_LOGGER.debug("async_get_options_flow called for entry %s", getattr(config_entry, "entry_id", "?"))
    return EDry2OptionsFlow(config_entry)
