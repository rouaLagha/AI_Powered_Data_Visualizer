# Conversion automatique RDL vers TWB (architecture multi-agent)

Ce projet implemente une chaine complete pour convertir un fichier SSRS `.rdl` vers un fichier Tableau `.twb`.

## Architecture

1. Parsing source
- Lit le RDL.
- Extrait data sources, datasets, champs, parametres, visuels et expressions.
- Utilise le schema `ReportDefinition.xsd` pour contraindre les types visuels extraits.

2. Agent LLM 1 (modelisation semantique)
- Entree: metadata parsee + resume des schemas RDL/TWB.
- Sortie:
  - `data_model.json`
  - `visual_model.json`
  - `mapping.json`

3. Agent LLM 2 (generation XML Tableau)
- Entree: les 3 JSON + resume TWB XSD.
- Sortie: XML TWB dans `generated_workbook.xml`.

4. Generation finale
- Controle structurel minimal du XML genere.
- Creation du fichier final `converted_report.twb`.

5. Publication optionnelle Tableau Server
- Si activee dans la config, le pipeline peut:
  - creer un extract `.hyper` depuis les tables SQL referencees
  - rewriter le workbook pour pointer vers cet extract
  - packager un `.twbx`
  - publier le workbook/package dans Tableau Server/Cloud.
- Rapports generes:
  - `tableau_extract_report.json`
  - `tableau_publish_report.json`

## Prerequis

- Python 3.10+

## Installation

```bash
pip install -r requirements.txt
```

## Configuration LLM

Copiez le fichier exemple:

- `config/llm_config.example.json`

et remplacez `YOUR_API_KEY`.

Le format attend 2 agents (agent2 peut reutiliser la meme config qu agent1)
et peut inclure un bloc optionnel `tableau_server`:

```json
{
  "agent1": {
    "api_url": "https://api.openai.com/v1/chat/completions",
    "api_key": "YOUR_API_KEY",
    "model": "gpt-4o-mini",
    "temperature": 0.1,
    "timeout_seconds": 120
  },
  "agent2": {
    "api_url": "https://api.openai.com/v1/chat/completions",
    "api_key": "YOUR_API_KEY",
    "model": "gpt-4o-mini",
    "temperature": 0.1,
    "timeout_seconds": 120
  },
  "tableau_server": {
    "enabled": false,
    "server_url": "https://your-tableau-server",
    "site_content_url": "",
    "project_name": "Default",
    "project_id": "",
    "workbook_name": "converted_report",
    "build_hyper_extract": true,
    "hyper_max_rows_per_table": 200000,
    "publish_mode": "overwrite",
    "file_format": "auto",
    "auth_method": "pat",
    "pat_name": "YOUR_PAT_NAME",
    "pat_secret": "env:TABLEAU_PAT_SECRET",
    "username": "env:TABLEAU_USERNAME",
    "password": "env:TABLEAU_PASSWORD",
    "skip_connection_check": false,
    "as_job": false,
    "fail_on_error": false,
    "desktop_rpa_enabled": false,
    "desktop_rpa_tableau_exe": "env:TABLEAU_PUBLIC_EXE",
    "desktop_rpa_email": "env:TABLEAU_EMAIL",
    "desktop_rpa_password": "env:TABLEAU_PASSWORD",
    "desktop_rpa_timeout_seconds": 180,
    "desktop_rpa_publish_wait_seconds": 240,
    "desktop_rpa_launch_settle_seconds": 4
  }
}
```

Notes sur `tableau_server`:
- `enabled`: active ou desactive la publication.
- `auth_method` (ou `auth_type`): `pat` ou `username_password`.
- `project_id` est prioritaire sur `project_name` si les deux sont renseignes.
- `publish_mode`: `overwrite` ou `create_new`.
- `build_hyper_extract`: active la creation du `.hyper` puis package `.twbx`.
- `hyper_max_rows_per_table`: limite optionnelle de lignes extraites par table.
- `file_format`: `auto`, `twb` ou `twbx`.
- `pat_secret`, `username`, `password` acceptent le format `env:VARNAME`.
- `desktop_rpa_enabled`: active le fallback RPA Desktop automatique dans le pipeline (Tableau Public).
- `desktop_rpa_tableau_exe`: chemin vers l executable Tableau Desktop/Public (optionnel).
- `desktop_rpa_email`, `desktop_rpa_password`: identifiants RPA (optionnels; sinon `username/password`).
- `desktop_rpa_timeout_seconds`, `desktop_rpa_publish_wait_seconds`, `desktop_rpa_launch_settle_seconds`: delais RPA.
- `desktop_rpa_cdp_enabled`: active une tentative CDP (WebView2) avant fallback UIA/clavier.
- `desktop_rpa_cdp_port_start`, `desktop_rpa_cdp_port_end`: plage de ports localhost a sonder pour CDP.
- `desktop_rpa_cdp_timeout_seconds`: delai max pour decouverte endpoint CDP et actions CDP.

Note Tableau Public:
- Avec `https://public.tableau.com`, le workflow prepare les artefacts (`.hyper`/`.twbx`) mais la publication REST automatique n est pas disponible dans ce flux.
- Si `desktop_rpa_enabled=true`, le pipeline lance automatiquement le fallback Desktop RPA pour tenter la publication.
- Les rapports `tableau_publish_report.json` et `tableau_rpa_publish_report.json` contiennent le detail REST + RPA.
- Si la creation du `.hyper` echoue, le pipeline tente quand meme de construire un `.twbx` a partir du `.twb` pour conserver un artefact publiable via RPA/Desktop.

Option RPA Desktop (Windows):
- Un script best effort est disponible: `scripts/publish_tableau_public_desktop_rpa.py`.
- Il automatise Tableau Public Desktop: ouverture du `.twbx`, tentative de `Save to Tableau Public`, login, et validation du publish.
- Il detecte automatiquement un executable Tableau Public ou Tableau Desktop dans `Program Files` (ou via `--tableau-exe`).
- Cette approche depend de la langue/UI de Tableau Desktop et peut demander une verification manuelle (MFA/captcha/dialogs).

Exemple:

```bash
python scripts/publish_tableau_public_desktop_rpa.py --twbx output/converted_report.twbx
```

Variables utiles:
- `TABLEAU_PUBLIC_EXE` (chemin vers l executable Tableau Public Desktop)
- `TABLEAU_EMAIL`
- `TABLEAU_PASSWORD`

## Execution

### Interface React

```bash
python react_sql_model_app.py
```

Puis ouvrez:

```text
http://127.0.0.1:5177
```

### Interface Streamlit (simple)

```bash
streamlit run streamlit_app.py
```

Puis dans l interface:
- uploader le fichier `.rdl`
- laisser `Enable publish stage` decoche pour les tests conversion-only
- cliquer `Convert`

### Mode multi-agent LLM

```bash
python run_conversion.py --rdl path/to/report.rdl --config config/llm_config.example.json
```

Mode conversion-only (sans extract/publish/RPA):

```bash
python run_conversion.py --rdl path/to/report.rdl --config config/llm_config.json --no-publish
```

Si `--config` n est pas fourni, le CLI prend automatiquement:
- `config/llm_config.json` si le fichier existe
- sinon `config/llm_config.example.json`

## Fichiers generes

Dans `output/`:

- `parsed_rdl.json`
- `rdl_xsd_summary.json`
- `twb_xsd_summary.json`
- `data_model.json`
- `visual_model.json`
- `mapping.json`
- `generated_workbook.xml`
- `converted_report.twb`
- `converted_report.hyper` (si extract active)
- `converted_report.twbx` (si extract active)
- `tableau_extract_report.json`
- `tableau_publish_report.json`
- `tableau_rpa_publish_report.json`

## Notes importantes

- Les schemas XSD fournis sont utilises a deux niveaux:
  - parsing: filtrage guide des visuels RDL autorises
  - prompt engineering: grounding des 2 agents LLM avec contraintes structurelles
- La validation XSD complete n est pas incluse ici (option possible via `lxml` si besoin).

## Boucle de validation XML

Le pipeline applique maintenant une boucle automatique `validate -> repair -> revalidate` sur le XML TWB genere.

- sortie de diagnostic: `output/validation_report.json`
- en mode LLM: tentative de reparation par Agent-2 si des erreurs persistent
- en mode fallback: reparation deterministe locale uniquement

## Utiliser un modele local (ex: Llama)

Vous pouvez configurer `agent2` vers un endpoint local compatible OpenAI Chat Completions (ex: Ollama proxy, LM Studio server, vLLM, etc.) via `config/llm_config.example.json`.

Exemple type:

```json
{
  "agent2": {
    "api_url": "http://localhost:1234/v1/chat/completions",
    "api_key": "",
    "model": "llama3.1:70b",
    "temperature": 0.1,
    "timeout_seconds": 180
  }
}
```
