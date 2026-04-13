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

Le format attend 2 agents (agent2 peut reutiliser la meme config qu agent1):

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
  }
}
```

## Execution

### Interface Streamlit (simple)

```bash
streamlit run streamlit_app.py
```

Puis dans l interface:
- uploader le fichier `.rdl`
- cliquer `Convert`

### Mode multi-agent LLM

```bash
python run_conversion.py --rdl path/to/report.rdl --config config/llm_config.example.json
```

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
