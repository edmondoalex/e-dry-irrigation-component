# Development log

## 2026-06-03

- Creato repository installabile per custom integration Home Assistant.
- Copiati solo i sorgenti attivi da `config/custom_components/e-dry_irrigation`.
- Rinominata la cartella installabile in `custom_components/e_dry`, coerente con `manifest.json` e `const.py`.
- Aggiunti README, CHANGELOG e `.gitignore`.
- Aggiunto `hacs.json` con dominio `e_dry`.
- Check globale: bump a `10.0.1` e completata la documentazione dei servizi registrati.
- Meteo smart calc migrato ai sensori e-SunMind di default; vento e-SunMind in `m/s` convertito a `km/h` per la soglia esistente.
- Opzioni component aggiornate per mostrare i sensori e-SunMind come default di taratura.
- Bump component a `10.0.3` per allineamento release con add-on e-Dry `10.0.3`.
- Bump component a `10.0.4`: aggiunta cache meteo e-SunMind `/api/weather/irrigation`, blocchi professionali e SmartCalc evoluto con ET0/pioggia/forecast/score.
- Bump component a `10.0.5`: ripristinati `icon.png` e `logo.png` del brand e-Dry nel custom component.
- Bump component a `10.0.6`: replicata struttura immagini di `e-Tende Intelligenti` con asset root, `docs/assets/` e `custom_components/e_dry/brand/`.
- Bump component a `10.0.7`: aggiunte traduzioni `strings.json` e `translations/it.json` per rendere leggibili le opzioni meteo/SmartCalc in Home Assistant.
- Bump component a `10.0.8`: aggiunto servizio `e_dry.update_weather_settings` per permettere all'add-on di modificare tarature meteo e SmartCalc.
- Bump component a `10.0.9`: aggiunti preset zona integrati/custom persistenti, assegnazione `profile_id` per zona e moltiplicatore SmartCalc per comportamento zona.
- Bump component a `10.0.10`: il preset zona resta applicato anche con `ignore_weather=true`; viene ignorato solo il fattore SmartCalc meteo.
- Bump component a `10.0.11`: aggiunto nel brand il logo EKONEX traslucido utilizzato dall'add-on.
