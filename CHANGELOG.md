# Changelog

## 10.0.8 - 2026-06-03

- Aggiunto servizio `e_dry.update_weather_settings` per modificare tarature meteo/SmartCalc da add-on o automazioni.
- Documentato il servizio in `services.yaml`.
- Bump versione component a `10.0.8`.

## 10.0.7 - 2026-06-03

- Aggiunti `strings.json` e `translations/it.json` per mostrare nomi leggibili nelle opzioni Home Assistant.
- Le tarature meteo e SmartCalc non appaiono piu solo come chiavi tecniche (`forecast_rain_skip_mm`, ecc.).
- Bump versione component a `10.0.7`.

## 10.0.6 - 2026-06-03

- Allineata gestione immagine al modello `e-Tende Intelligenti`: aggiunti asset in root, `docs/assets/` e `custom_components/e_dry/brand/`.
- README aggiornato con immagine e-Dry in apertura.
- Bump versione component a `10.0.6`.

## 10.0.5 - 2026-06-03

- Ripristinati `icon.png` e `logo.png` nel custom component per il brand e-Dry in Home Assistant/HACS.
- Bump versione component a `10.0.5`.

## 10.0.4 - 2026-06-03

- Meteo professionale: il component legge in background e-SunMind `GET /api/weather/irrigation` come fonte primaria.
- Aggiunte soglie configurabili per freschezza dato, pioggia prevista 24h e pioggia recente 24h.
- SmartCalc aggiornato con ET0, pioggia recente, pioggia prevista, temperatura, umidita, radiazione solare, vento e weather score.
- `sensor.e_dry_meteo_info` espone endpoint, modalita meteo, source, age, blocchi, score e dettagli SmartCalc.
- README aggiornato con funzionamento e taratura completa.

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
