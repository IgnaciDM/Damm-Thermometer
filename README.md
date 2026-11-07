# Pulso

**Pulso** is a local, Python-powered prototype for responsible workplace listening. It turns an HR need into a short, context-aware survey, requires human review before publication, and provides aggregate participation and trend insights.

## Run it

Requires Python 3.10 or newer.

```powershell
python -m pip install -r requirements.txt
python -m backend.app
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the aggregate dashboard or [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin) to design a survey. The initial data is synthetic (600 responses across two periods and five cohorts), clearly labelled, and never mixed into live response counts.

The HR flow supports a defined listening need, configurable population shares, 3–6 editable questions, conditional follow-up questions, explicit approval, QR/link sharing, local Typeform-style participation, and (when configured) Typeform publication and response import. Recommendations must also be approved or dismissed by a human before they are treated as actions.

## Project structure

```text
backend/
  app.py                 Python server, API, and SQLite persistence
  scripts/
    qrgenerator.py       Optional QR image generator
ui/
  index.html             Aggregate dashboard and check-in interface
  app.js                 Dashboard and respondent interactions
  admin.html             HR survey design and insights
  admin.js               Survey editor and admin API client
  admin.css              Responsive admin design
  styles.css             Responsive visual design
  assets/
    codigo_qr.png        Optional static QR image
tests/
  test_app.py            Backend tests
data/
  pulso.sqlite3          Local response database (created at runtime)
```

The server serves the UI directly; no separate frontend build is required. The respondent experience is a Typeform-style, one-question-at-a-time conversation. Existing root-level database files are moved to `data/pulso.sqlite3` on the next launch, without overwriting an existing database there.

## Generate questions with an API

Without credentials, Pulso uses curated local survey templates. In **Conexiones** on `/admin`, optionally enter an OpenAI-compatible chat-completions API key, model, and endpoint to generate draft surveys, and/or a Typeform API token to publish to Typeform. Credentials stay in server memory and are discarded when the server stops.

Alternatively, set these environment variables before starting the server:

```powershell
$env:AI_API_KEY = "your-key"
$env:AI_BASE_URL = "https://api.openai.com/v1/chat/completions"
$env:AI_MODEL = "gpt-4o-mini"
$env:TYPEFORM_API_TOKEN = "your-typeform-token"
$env:PULSO_PUBLIC_URL = "https://your-public-host"
python -m backend.app
```

`PULSO_PUBLIC_URL` is needed when local surveys/QR codes must be reachable outside the machine running Pulso. Without it, local links use the request host. Typeform-hosted surveys use Typeform's public link.

The server binds to `127.0.0.1` only. Do not expose this prototype's HR endpoints to a network without adding authentication and deployment hardening. Responses are stored locally for up to 90 days; names, email addresses, phone numbers, URLs, and common self-identifying phrases are removed from free-text comments before storage. Only aggregated results are shown, and groups under five responses are hidden. Topic labels are keyword-based signals, not causal findings.

## Verify

```powershell
python -m unittest discover -s tests -v
```
