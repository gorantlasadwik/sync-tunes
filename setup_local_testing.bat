@echo off
echo Setting up local HTTPS testing for Sync Tunes...
echo.

echo 1. Creating .env file from template...
copy local_env_example.txt .env

echo 2. Installing required packages...
pip install -r requirements.txt

echo 3. Initializing database...
python init_db.py

echo.
echo ✅ Setup complete!
echo.
echo To start the HTTPS server, run:
echo    python run_local_https.py
echo.
echo The script will give you an HTTPS URL to use for Spotify OAuth testing.
echo.
pause
