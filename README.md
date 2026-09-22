# 🧠 NeuroGames — Full-Stack ADHD Assessment & MLOps Platform

A serious games platform for cognitive assessment and ADHD clinical profiling, backed by a production-ready MLOps pipeline, automated model orchestration, and full-stack observability.

---

## 📐 Platform Architecture

```
ADN-Expertise/
├── neurogames-app/          → Next.js Web App (5 Cognitive Canvas Games + Clinical Portal)
│   └── frontend/            → Next.js 14, React 19, TypeScript, CSS Modules (:3001)
├── mlops/                   → FastAPI Backend Server (:8000), Data Ingestion & Drift Detection
├── multigame_pipeline/      → ML Classification Models, Anomaly Detection & Feature Extraction
├── dags/                    → Apache Airflow DAGs for Model Retraining & Orchestration
├── monitoring/              → Prometheus, Grafana Dashboards & Tempo Distributed Tracing Configs
├── docker-compose-airflow.yaml → Multi-container setup (Airflow, Postgres, Prometheus, Grafana, Tempo)
└── start_mlflow.bat         → MLflow Experiment Tracking Server Launcher (:5000)
```

---

## ⚡ Quick Start Guide

### Prerequisites

* **Python**: 3.10+ (with virtual environment `venv` configured in project root)
* **Node.js**: 18+ and `npm`
* **Docker & Docker Compose**: Required for Airflow, Grafana, Prometheus, and PostgreSQL containers.

---

### Step 1: Environment Setup

Ensure dependencies are installed in your Python environment:

```powershell
# From project root directory
.\venv\Scripts\activate
pip install -r requirements.txt
```

---

### Step 2: Start MLOps Backend API

Launch the FastAPI backend server using standard `uvicorn`:

```powershell
# Production / Standard mode
python -m uvicorn mlops.server:app --host 0.0.0.0 --port 8000

# Development mode (with live reloading)
python -m uvicorn mlops.server:app --host 0.0.0.0 --port 8000 --reload
```

* **API Endpoint:** `http://localhost:8000`
* **Interactive Documentation (Swagger):** `http://localhost:8000/docs`

---

### Step 3: Start Frontend Web Application

Open a second terminal window and navigate to the Next.js frontend directory:

```powershell
cd neurogames-app/frontend

# Install dependencies (first run only)
npm install

# Start Next.js development server on port 3001
npm run dev -- -p 3001
```

* **Web Application:** `http://localhost:3001`

---

### Step 4: Start MLflow Experiment Tracking Server

Open a third terminal window to launch MLflow for tracking model parameters and metrics:

```powershell
.\start_mlflow.bat
```

* **MLflow UI:** `http://localhost:5000`

---

### Step 5: Start Airflow & Monitoring Stack (Docker)

To launch Apache Airflow, PostgreSQL, Prometheus, Grafana, and Tempo:

```powershell
# Initialize Airflow DB & User (First time only)
docker-compose -f docker-compose-airflow.yaml up airflow-init

# Start all containers in background mode
docker-compose -f docker-compose-airflow.yaml up -d
```

* **Airflow UI:** `http://localhost:8085` *(Credentials: `airflow` / `airflow`)*
* **Grafana Dashboards:** `http://localhost:3000` *(Credentials: `admin` / `neurogames`)*
* **Prometheus Metrics:** `http://localhost:9090`

---

## 🔍 How to Explore Features

### 1. 🎮 Cognitive Games & Web App (`http://localhost:3001`)

* **Login Page (`/`)**: Choose an avatar and enter a player name to begin a session.
* **Game Carousel (`/games`)**: Access 5 cognitive assessment games built with HTML5 Canvas:
  1. 🛑 **Go/No-Go**: Evaluates inhibitory control and reaction time.
  2. 🧠 **Memory Matrix**: Measures working memory capacity and visual-spatial recall.
  3. 🎯 **Tracking**: Tests visual sustained attention and motion tracking precision.
  4. 📐 **Shapes**: Assesses spatial reasoning and pattern matching.
  5. 🧩 **Puzzle**: Tests executive planning and cognitive flexibility.
* **Pre-Game Overlay**: Clear instructions before each game starts (accessible anytime via `?` tooltip).
* **Player Profile & Badges (`/profile`)**: View cumulative XP, level progression, streaks, and 10 earnable milestone badges.
* **Session History (`/history`)**: Review timeline of past game attempts, scores, and accuracy metrics.
* **Clinical Report (`/rapport`)**: Generate an AI-powered executive report breaking down cognitive domains, ADHD risk indicators, and performance trends.

---

### 2. ⚙️ MLOps Backend & API (`http://localhost:8000/docs`)

* **Interactive API Playground**: Test endpoints directly via Swagger UI:
  * `POST /api/v1/games/submit`: Submit raw game session telemetry.
  * `GET /api/v1/participants`: List registered players and session counts.
  * `GET /api/v1/clinical/report`: Request cognitive assessment analysis.
  * `GET /api/v1/ml/models/status`: Inspect active champion model details.
  * `POST /api/v1/ml/drift/check`: Run input data drift analysis.

---

### 3. 🤖 MLOps Pipeline & Drift Detection

* **ML Model Training & Evaluation**: Supervised classifiers infer ADHD risk probability from multi-game behavioral features (latency, error rate, variability).
* **Drift Detection (Evidently AI)**: Automated feature drift analysis comparing incoming player telemetry against baseline training distributions.
* **Experiment Tracking (MLflow — `:5000`)**: Compare experiment runs, hyperparameter tuning logs, ROC-AUC curves, and registered candidate models.

---

### 4. 🔄 Workflow Orchestration (Airflow — `:8085`)

* **Scheduled DAG Workflows**:
  * Automated data extraction from PostgreSQL.
  * Continuous model evaluation and retraining.
  * Automated champion model updates and drift report generation.
* **Credentials**: Username `airflow`, Password `airflow`.

---

### 5. 📊 Observability & Monitoring (Grafana — `:3000`)

* **Pre-configured Dashboards**:
  * **System Telemetry**: API request rates, latency, HTTP response codes, server health.
  * **ML Drift Alerts**: Visual breakdown of drifted features across game domains.
* **Credentials**: Username `admin`, Password `neurogames`.

---

## 📌 Service Directory & Ports Summary

| Service                     | Port     | URL                                                     | Description                        | Default Credentials        |
| :-------------------------- | :------- | :------------------------------------------------------ | :--------------------------------- | :------------------------- |
| **Frontend Web App**  | `3001` | [http://localhost:3001](http://localhost:3001)           | Next.js Serious Games Portal       | —                         |
| **MLOps Backend API** | `8000` | [http://localhost:8000](http://localhost:8000)           | FastAPI Service                    | —                         |
| **API Swagger Docs**  | `8000` | [http://localhost:8000/docs](http://localhost:8000/docs) | Interactive API Explorer           | —                         |
| **MLflow Server**     | `5000` | [http://localhost:5000](http://localhost:5000)           | ML Experiment Tracking & Artifacts | —                         |
| **Apache Airflow**    | `8085` | [http://localhost:8085](http://localhost:8085)           | Workflow & DAG Orchestrator        | `airflow` / `airflow`  |
| **Grafana**           | `3000` | [http://localhost:3000](http://localhost:3000)           | Observability & Metrics Dashboards | `admin` / `neurogames` |
| **Prometheus**        | `9090` | [http://localhost:9090](http://localhost:9090)           | Metrics Scraper & Query Engine     | —                         |
| **PostgreSQL DB**     | `5433` | `localhost:5433`                                      | Runtime Database Storage           | `postgres` / `aaaa`    |
