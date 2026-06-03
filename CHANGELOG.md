# Changelog

## 10.0.3 - 2026-06-03

- Bump versione custom component a `10.0.3` per allineamento con add-on e-Dry `10.0.3`.
- Nessuna modifica funzionale: la logica meteo component resta sui sensori e-SunMind configurabili.

## 10.0.2 - 2026-06-03

- Smart calc e blocchi meteo usano di default i sensori e-SunMind `sensor.e_sunmind_weather_*`.
- Normalizzato il confronto vento: i valori `m/s` vengono convertiti in `km/h` prima del confronto con la soglia storica.
- Il pannello opzioni propone i sensori e-SunMind come default per rain/temp/humidity/wind.

## 10.0.1 - 2026-06-03

- Bump versione custom component a `10.0.1`.
- Documentati in `services.yaml` i servizi gia registrati `remove_stale_entities`, `request_event_log` e `clear_event_log`.

## 0.1.0 - 2026-06-03

- Prima pubblicazione Git del custom component `e_dry`.
- Normalizzata la struttura installabile in `custom_components/e_dry/`.
- Esclusi backup locali, zip, log, cache Python e file temporanei.
- Aggiunto `hacs.json` per supportare l'uso come custom repository HACS.
