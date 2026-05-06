# Backend REST API

Ce dossier contient le backend HTTP JSON utilise par l interface React.

## Structure

```text
backend/
  assets/
  config/
  scripts/
  src/
  sql_model_api/
    main.py
    api/
      routes.py
```

- `main.py`: serveur HTTP, endpoints REST, et service des fichiers statiques React.
- `api/routes.py`: declaration des endpoints et dispatch POST centralise.
- `run_react_sql_model_app.py`: lanceur backend equivalent a `python -m backend.sql_model_api.main`.
- `run_qlik_conversion.py`: lanceur du pipeline Qlik Sense QVF vers `qlik_metadata.json` via Qlik Engine API / QIX.
- Les artefacts generes sont ecrits dans `../outputs/`.
  Le flux Qlik ecrit ses artefacts demandes dans `../output/`.

## Lancer le backend

Depuis la racine du projet:

```bash
python -m backend.sql_model_api.main
```

ou:

```bash
python backend/run_react_sql_model_app.py
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
POST /api/conversion/run
POST /api/qlik/metadata/run
POST /api/rdl-editor/apply
```

## Qlik Sense QVF vers qlik_metadata.json

Le flux Qlik n'ouvre pas le `.qvf` comme XML/ZIP et ne propose pas de mode mock.
Il stocke le fichier uploade dans un dossier de job, rend le fichier ouvrable
par Qlik Sense Desktop, puis extrait les metadonnees par Qlik Engine API / QIX
(`OpenDoc`, `GetScript`, `GetAllInfos`, `GetObject`, `CreateSessionObject`,
`GetLayout`). Si QIX ne repond pas, le job echoue au lieu de produire un
artefact simule ou deterministe.

Prerequis local:

- Qlik Sense Desktop ou un Qlik Engine accessible.
- Pour Desktop: Qlik lance sur `ws://localhost:4848/app`.
- Le fichier `.qvf` dans `Documents\Qlik\Sense\Apps`, ou un dossier Apps fourni avec `--qlik-apps-dir`.

```bash
python backend/run_qlik_conversion.py --qvf C:\Users\Roua\Documents\Qlik\Sense\Apps\test02.qvf --jobs-root output\qlik_jobs --qlik-endpoint ws://localhost:4848/app
```

Artefacts generes:

```text
output/qlik_jobs/<job_id>/upload/<source>.qvf
output/qlik_jobs/<job_id>/qlik_metadata.json
output/qlik_jobs/<job_id>/job.json
```
