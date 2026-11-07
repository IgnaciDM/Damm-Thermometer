# Pulso

**Pulso** is a local, Python-powered prototype for measuring workplace climate continuously and responsibly. It turns the six dimensions in the challenge brief into short, context-aware check-ins and an aggregate insights dashboard for factories, logistics, commercial teams, and offices.

## Run it

Requires Python 3.10 or newer. The app uses only the Python standard library.

```powershell
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first launch the dashboard shows clearly labelled synthetic demo data. The first submitted check-in removes that sample data; real responses are then collected without names, email addresses, or free-text comments.

## Generate questions with an API

Without credentials, Pulso uses a clearly labelled set of curated demo questions. To use an OpenAI-compatible chat-completions API, choose **Configurar API** in the app and enter the endpoint, model, and API key. The key stays in server memory only and is discarded when the server stops.

Generated questions can also be opened in a Microsoft Teams chat draft using the **Compartir en Teams** link; sharing is always a deliberate user action. The dashboard suggests a small, practical next step when a dimension needs attention.

Alternatively, set these environment variables before starting the server:

```powershell
$env:AI_API_KEY = "your-key"
$env:AI_BASE_URL = "https://api.openai.com/v1/chat/completions"
$env:AI_MODEL = "gpt-4o-mini"
python app.py
```

The server binds to `127.0.0.1` only. Responses are stored in a local SQLite database (`pulso.sqlite3`); only a date, work context, climate dimension, and 1–5 score are retained. Results for groups with fewer than five responses are hidden. No employee identifiers or raw comments are collected.

## Verify

```powershell
python -m unittest discover -s tests -v
```
