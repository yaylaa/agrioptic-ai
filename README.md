# AgriOpticAI

**A data-intensive decision-support prototype, built end to end by Faruk Ergün.**

Python / FastAPI · TypeScript / React · NumPy / SciPy · Satellite and weather APIs

## The problem

Uncertain canal delivery changes which irrigation actions are operationally feasible. AgriOpticAI connects field evidence, explicit modeling assumptions, and constrained planning in an interactive workflow. The original case study covers three cotton parcels (approximately 30 hectares) in Turkey and the completed 2026 season.

## What I built

- A React/TypeScript analytical interface with maps, time-series exploration, scenario controls, and comparisons.
- A FastAPI backend with Pydantic validation and integrations for Copernicus Sentinel-2 and Open-Meteo.
- Server-side data retrieval, caching, offline fallbacks, and source metadata.
- A NumPy/SciPy mixed-integer optimizer that compares discrete irrigation events under delivery, equipment, timing, and cost assumptions.
- Spatial investigation zones, model explanations, exports, and automated backend and input-parser tests.

## Architecture

```text
React / TypeScript interface
             |
      FastAPI / Pydantic
             |
    +--------+------------------+
    |                           |
Provider integrations      Decision engine
Sentinel-2 / Open-Meteo     Water balance / SciPy MILP
    |                           |
Cache + provenance         Feasible events + explanations
```

## Start reviewing here

| Area | Source |
| --- | --- |
| API contracts and validation | [backend/main.py](backend/main.py) |
| Satellite integration and error handling | [backend/satellite.py](backend/satellite.py) |
| Weather retrieval and fallback | [backend/weather.py](backend/weather.py) |
| Optimization and plan verification | [backend/decision.py](backend/decision.py) |
| Crop-water model | [backend/water_balance.py](backend/water_balance.py) |
| Scenario interface | [frontend/src/WaterLab.tsx](frontend/src/WaterLab.tsx) |
| Typed scenario interpretation | [frontend/src/assistantScenario.ts](frontend/src/assistantScenario.ts) |
| Backend tests | [tests](tests) |

## Public repository scope

This is the **source-code portfolio edition**. Private parcel polygons, field-linked satellite caches, weather snapshots, farmer interview records, credentials, and personal documents are deliberately excluded. The complete local pilot retains those inputs. This public checkout alone does **not** run the full pilot dashboard or dataset-dependent backend tests; it is intended for architecture and implementation review. There is no public production deployment.

The frontend can be built independently:

```bash
pnpm install --frozen-lockfile
pnpm --dir frontend build
node --test frontend/tests/assistantScenario.test.mjs
```

Recommended prerequisites: Node.js 22+ and pnpm 11.

### Running with authorized pilot inputs

For the complete application, restore authorized `pilot_parcels.geojson`, `data/weather_2026.csv`, `data/weather_2026_source.json`, and the other pilot snapshots/scenario defaults to their original paths. Python 3.11/3.12 is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-api.txt
pnpm install --frozen-lockfile
pnpm --dir frontend build
sh scripts/start.sh
```

Then open http://localhost:8000. Copernicus refresh requires backend environment configuration; never put credentials in frontend code. The legacy Streamlit implementation is retained in `app.py` and `baseline_app.py`.

## Evidence and boundaries

- Observations, model estimates, illustrative assumptions, and roadmap demos are labeled separately.
- Operational and root-zone assumptions require field validation. Optimization output is not a validated agronomic prescription.
- Yield/fertilizer demonstrations are exploratory; no verified savings, commercial adoption, or yield improvement is claimed.
- The conversational interface uses a limited local phrase parser. It is **not** an LLM integration, autonomous agent system, or general language-understanding model.
- The original cotton case study is retrospective; it does not advise irrigating an already harvested crop today.

## Next engineering steps

Generalize dataset onboarding, add explicitly synthetic public fixtures, and separate the decision engine from pilot-specific presentation. These are future improvements, not completed features.
