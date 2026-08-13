@echo off
echo =============================================
echo   DummyReport Demo Portal
echo =============================================
echo Starting Streamlit on http://localhost:8501
if exist .venv\Scripts\streamlit.exe (
    .venv\Scripts\streamlit.exe run app.py --server.port 8501
) else (
    streamlit run app.py --server.port 8501
)
pause
