@echo off
REM Start the MLflow tracking server backed by PostgreSQL
REM Run this FIRST before the pipeline or dashboard.
REM Then visit http://127.0.0.1:5000

cd /d "%~dp0"
call venv\Scripts\activate.bat

set "ARTIFACT_ROOT=%CD%\mlflow_artifacts"
set "ARTIFACT_URI=file:///%ARTIFACT_ROOT:\=/%"

echo.
echo ============================================================
echo   MLflow Tracking Server
echo   Backend: postgresql+psycopg://localhost:5433/neurogames
echo   UI:      http://127.0.0.1:5000
echo ============================================================
echo.

mlflow server ^
    --backend-store-uri postgresql+psycopg://postgres:aaaa@localhost:5433/neurogames ^
    --default-artifact-root "%ARTIFACT_URI%" ^
    --host 0.0.0.0 ^
    --port 5000 ^
    --allowed-hosts "host.docker.internal:5000,host.docker.internal:*,localhost:*,127.0.0.1:*"

pause
