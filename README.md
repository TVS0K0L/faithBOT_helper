# faithBOT helper

Every 30 minutes GitHub Actions runs `faithbot_cloud.py`. It turns big Telegram sermon videos and YouTube audio into MP3 files and sends them to faithBOT.

The secrets live in Settings → Secrets and variables → Actions: `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`, `FAITHBOT_URL` and `FAITHBOT_KEY`. Never put them in a file here.

`login.py` is run once on a Mac to get `TG_SESSION`.

## Backlog

Send `/backlog` to faithBOT and each run also fetches older Горница and morning-word videos from before the cut-off date, newest first, into faithBOT/Архив. Each run stops after about 30 minutes and the next run continues from where it stopped. To read the morning word from a private group, send faithBOT `/source <post link>`.

## The backlog (turbo)

`turbo.py` (workflow "faithBOT backlog") writes the full notes of every old Кафедра, Горница and morning word, using faithBOT's own prompts, and faithBOT saves them quietly. It needs one more secret, `GEMINI_KEY`. A run lasts about 5.5 hours and a new one starts every 6 hours. Send `/progress` to faithBOT to see how many are done and when the backlog should finish.
