# 🧠 NeuroGames

A web-based serious games platform for ADHD cognitive assessment, backed by a full MLOps pipeline.

## Structure

```
neurogames-app/      →  Next.js web app (5 games + admin dashboard)
mlops/               →  FastAPI backend, data ingestion, drift monitoring
multigame_pipeline/  →  ML classification, anomaly detection, assessment
dags/                →  Airflow DAGs (scheduling)
monitoring/          →  Prometheus + Grafana observability
data/                →  development fixtures and game module artifacts
```

## Data Flow

PostgreSQL is the runtime source of truth. CSV files are development fixtures
only; the normal local path seeds PostgreSQL directly with calibrated fake data:
`python tools/seed_fake_data.py --clear-all-child-data`. See `docs/data_flow.md`.

## Quick Start

```bash
# Backend
pip install -r requirements.txt
./tools/start_mlops_backend.ps1

# Frontend
cd neurogames-app/frontend
npm install && npm run dev -- -p 3001

# MLflow
./start_mlflow.bat
```

| Service | URL |
|---------|-----|
| Games | http://localhost:3001 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

### Backend Reload Warning

Do not run the MLOps backend with unrestricted `uvicorn --reload` while Airflow
is running pipeline tasks. The reload watcher can detect changes in `dags/`,
restart the API server, and interrupt background ML workers with
`KeyboardInterrupt`.

Use the normal launcher for Airflow and MLOps demos:

```powershell
.\tools\start_mlops_backend.ps1
```

For API development only, use scoped reload so DAG edits do not restart the
server:

```powershell
.\tools\start_mlops_backend.ps1 -Reload
```

## Games

| Game | Cognitive Domain |
|------|-----------------|
| Go/No-Go | Inhibitory control |
| Memory | Working memory |
| Tracking | Visual attention |
| Shapes | Spatial reasoning |
| Puzzle | Planning & flexibility |

## Tech Stack

**Frontend:** Next.js · TypeScript · Plotly.js  
**Backend:** FastAPI · PostgreSQL · MLflow  
**ML:** scikit-learn · Evidently (drift)  
**Infra:** Docker · Airflow · Prometheus · Grafana · GitLab CI/CD
