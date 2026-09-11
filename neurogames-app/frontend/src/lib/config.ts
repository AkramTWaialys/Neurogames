export const CONFIG = {
  API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000",
  MLFLOW_URL: process.env.NEXT_PUBLIC_MLFLOW_URL || "http://localhost:5000",
  AIRFLOW_URL: process.env.NEXT_PUBLIC_AIRFLOW_URL || "http://localhost:8085",
  GRAFANA_URL: process.env.NEXT_PUBLIC_GRAFANA_URL || "http://localhost:3000",
};
