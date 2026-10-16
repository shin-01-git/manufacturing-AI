"""Streamlit Community Cloud 진입점.

배포 설정의 Main file path: D-part/factory-energy-copilot/streamlit_app.py
같은 폴더의 requirements.txt로 패키지를 설치하고, 실제 화면은 src/app/app.py가 그린다.
로컬 실행은 기존처럼 `python -m streamlit run src/app/app.py`.
"""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "src" / "app" / "app.py"), run_name="__main__")
