@echo off
cd /d C:\Users\daflo\nfl_odds_tracker
call .\.venv\Scripts\activate
python .\odds_pull.py
python .\edges_scan.py   # <- run edge finder immediately after
