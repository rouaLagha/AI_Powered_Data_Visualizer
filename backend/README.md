# Backend REST API

Ce dossier contient le backend HTTP JSON utilise par l interface React.

## Structure

```text
backend/
  sql_model_api/
    main.py
    api/
      routes.py
```

- `main.py`: serveur HTTP, endpoints REST, et service des fichiers statiques React.
- `api/routes.py`: declaration des endpoints et dispatch POST centralise.
- `react_sql_model_app.py`: lanceur racine garde pour compatibilite.

## Lancer le backend

Depuis la racine du projet:

```bash
python -m backend.sql_model_api.main
```

ou:

```bash
python react_sql_model_app.py
```

URL par defaut:

```text
http://127.0.0.1:5177
```

## Endpoints REST

```text
GET  /api/state
GET  /api/file?path=...
POST /api/reset
POST /api/rdl/parse
POST /api/dataset/select
POST /api/model/analyze
POST /api/schema/validate
POST /api/twb/generate
POST /api/tableau/publish
```
