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
