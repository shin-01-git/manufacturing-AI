# RTU Energy Forecast

This project aggregates RTU telemetry into hourly factory energy estimates, evaluates forecasting models against May ground truth, and displays forecasts in a Streamlit dashboard.

## Data privacy and GitHub

The raw telemetry file is approximately 4.9 GB, above GitHub's 100 MB per-file limit. The raw files and generated outputs are excluded by `.gitignore` because they contain operational energy data. Do not publish them, including through Git LFS, unless you have authorization and have reviewed the repository's visibility and data-handling requirements.

Provide these files locally before running the pipeline:

- `rtu_data_full.csv`
- `rtu_ground_truth_may.csv`

They should be placed in this project directory. The forecast outputs are generated locally under `forecast_outputs/` and are ignored by Git.

## Setup on Windows

The project was last verified with Python 3.9.19. From a fresh clone, run these commands in PowerShell:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks environment activation, run the commands below instead, using the venv's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

After placing both data files in the project directory:

```powershell
python rtu_hourly_forecast.py
python -m streamlit run forecast_dashboard.py
```

Open the local URL printed by Streamlit. The pipeline writes model comparisons, May evaluation, and forecast windows to `forecast_outputs/`.

## Project files

- `rtu_hourly_forecast.py`: aggregation, features, model evaluation, and forecast generation
- `forecast_dashboard.py`: interactive forecast dashboard
- `forecast_pipeline_document.md`: methodology, unit assumptions, validation notes, and known limitations
- `requirements.txt`: pinned Python dependencies
