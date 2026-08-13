@echo off
echo =============================================
echo   DummyReport Demo Setup
echo =============================================
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Setup complete.
echo Start the app with: streamlit run app.py
pause
