ECHO PLANA STARTER

set VENV_DIR=.venv

if not exist "%VENV_DIR%" (
    ECHO Creating virtual environment in '%VENV_DIR%' folder...
    python -m venv %VENV_DIR%
    if %errorlevel% neq 0 (
        ECHO Failed to create virtual environment.
        pause
        exit /b
    )
)

ECHO Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"

ECHO Installing/Updating required packages...
python -m pip install -U -r requirements.txt

ECHO Starting the PLANA...
python main.py
pause