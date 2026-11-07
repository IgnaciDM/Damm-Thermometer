# Pulso

**Pulso** is a local, Python-powered prototype for measuring workplace climate continuously and responsibly. It turns the six dimensions in the challenge brief into short, context-aware check-ins and an aggregate insights dashboard for factories, logistics, commercial teams, and offices.

## Run it

Requires Python 3.10 or newer. The app uses only the Python standard library.

```powershell
python -m backend.app
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first launch the dashboard shows clearly labelled synthetic demo data. The first submitted check-in removes that sample data; real responses are then collected without names, email addresses, or free-text comments.

## Project structure

```text
backend/
  app.py                 Python server, API, and SQLite persistence
  scripts/
    qrgenerator.py       Optional QR image generator
ui/
  index.html             Dashboard and check-in interface
  app.js                 Browser interactions and API client
  styles.css             Responsive visual design
  assets/
    codigo_qr.png        Generated QR image
tests/
  test_app.py            Backend tests
data/
  pulso.sqlite3          Local response database (created at runtime)
```

The server serves the UI directly; no separate frontend build or package install is required. The check-in opens as a Typeform-style, one-question-at-a-time conversation. Existing root-level database files are moved to `data/pulso.sqlite3` on the next launch, without overwriting an existing database there.

The optional QR generator needs `qrcode[pil]`:

```powershell
python -m pip install "qrcode[pil]"
python backend/scripts/qrgenerator.py
```

## Generate questions with an API

Without credentials, Pulso uses a clearly labelled set of curated demo questions. To use an OpenAI-compatible chat-completions API, choose **Configurar API** in the app and enter the endpoint, model, and API key. The key stays in server memory only and is discarded when the server stops.

Generated questions can also be opened in a Microsoft Teams chat draft using the **Compartir en Teams** link; sharing is always a deliberate user action. The dashboard suggests a small, practical next step when a dimension needs attention.

Alternatively, set these environment variables before starting the server:

```powershell
$env:AI_API_KEY = "your-key"
$env:AI_BASE_URL = "https://api.openai.com/v1/chat/completions"
$env:AI_MODEL = "gpt-4o-mini"
python -m backend.app
```

The server binds to `127.0.0.1` only. Responses are stored in `data/pulso.sqlite3`; only a date, work context, climate dimension, and 1–5 score are retained. Results for groups with fewer than five responses are hidden. No employee identifiers or raw comments are collected.

## Verify

```powershell
python -m unittest discover -s tests -v
```
