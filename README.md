# Victoria Reports

Small set of scripts to pre-calculate short reports from MongoDB events using OpenAI and expose the cached results through a lightweight Flask API.

## Features
- Pulls events from MongoDB and groups similar entries with fast fingerprints.
- Generates summaries with OpenAI models for four time windows: current event, last 3 hours, last 24 hours, and yesterday.
- Caches results in MongoDB to avoid unnecessary recomputation and keeps a history collection.
- Exposes read-only HTTP endpoints (Spanish legacy paths plus English aliases) for voice assistants or other clients.

## Requirements
- Python 3.10+
- MongoDB accessible through `MONGO_URI`
- OpenAI API key with access to the configured models

Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Environment Variables
Set these before running either service:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | API key for OpenAI. |
| `PROMPT_ANALYSIS` | System prompt used to steer the event analysis. |
| `MONGO_URI` | Mongo connection URI. |
| `MONGO_DB_NAME` | Database name. |
| `MONGO_COLL_NAME` | Collection containing raw events with a `timestamp` field. |
| `VICTORIA_APIKEY` | Shared secret for the Flask API. |

Optional tuning:
- `MODEL_ACTUAL` (default `gpt-4o-mini`)
- `MODEL_TRES` (default `gpt-4o-mini`)
- `MODEL_DIA` (default `gpt-4o-mini`)
- `MODEL_AYER` (default `gpt-4.1`)
- `CYCLE_SLEEP_SECONDS` (default `600`)
- `REQUEST_TIMEOUT_SECONDS` (default `40`)

## Running the pre-calculator
This loop fetches events, calls OpenAI when the input changes, and stores the results in `victoria_cache` plus a history in `victoria_cache_history`.
```bash
python preCalcultator.py
```

It uses the following cache types (stored under the Mongo field `tipo`): `actual`, `tres`, `dia`, `ayer`.

## Running the API server
Read-only server that returns the cached reports. Default port: `8888`.
```bash
python server.py
```

Endpoints (all expect `apikey` query parameter):
- `/informe_actual` and `/report/current`
- `/informe_tres` and `/report/three-hours`
- `/informe_dia` and `/report/day`
- `/informe_ayer` and `/report/yesterday`

## Notes
- Hashes used for cache comparison are now deterministic (`sha256` over sorted JSON) so the process does not recompute unnecessarily after restarts.
- Mongo field names remain in Spanish (`tipo`, `texto`) for compatibility with existing data; code and logs are now in English.
