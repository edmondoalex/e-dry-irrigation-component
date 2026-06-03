# e-Dry Irrigation custom component

Custom integration Home Assistant per il controllo irrigazione e-Dry.

Versione corrente: `10.0.3`.

## Installazione manuale

1. Copia la cartella `custom_components/e_dry` dentro la cartella `config/custom_components/` di Home Assistant.
2. Riavvia Home Assistant.
3. Aggiungi l'integrazione **e-dry Irrigation** dalla UI di Home Assistant.

La cartella deve essere:

```text
config/custom_components/e_dry/
```

Il dominio Home Assistant e' `e_dry`.

## Servizi principali

- `e_dry.start_zone`
- `e_dry.start_zone_for`
- `e_dry.stop_zone`
- `e_dry.update_zone`
- `e_dry.update_program`
- `e_dry.remove_stale_entities`
- `e_dry.request_event_log`
- `e_dry.clear_event_log`

## Integrazione con l'add-on dashboard

L'add-on dashboard e-Dry usa questa integrazione come backend funzionale. Il repository add-on e':

`https://github.com/edmondoalex/e-dry-irrigazione-addon`

La dashboard legge i sensori aggregati `sensor.e_dry_zones_info`, `sensor.e_dry_programs_info`, `sensor.e_dry_meteo_info` e invoca i servizi `e_dry.*`.
