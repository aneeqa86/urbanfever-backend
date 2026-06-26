# FastAPI Boilerplate

A clean, production-ready FastAPI boilerplate with Supabase and Anthropic integration.

## Stack
- **FastAPI** — web framework
- **Supabase** — Postgres database + auth
- **Anthropic** — Claude API
- **Pydantic v2** — data validation
- **APScheduler** — cron jobs
- **Uvicorn** — ASGI server

## Structure
```
app/
├── main.py               # App entry point
├── api/
│   └── v1/
│       ├── router.py     # Collects all routers
│       └── endpoints/    # One file per resource
├── core/
│   ├── config.py         # Settings from .env
│   └── database.py       # Supabase client
├── schemas/              # Pydantic request/response models
├── models/               # DB model definitions
└── services/             # Business logic layer
tests/
```

## Setup

```bash
# 1 — create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# 2 — install dependencies
pip install -r requirements.txt

# 3 — configure environment
cp .env.example .env
# fill in your values in .env

# 4 — run dev server
uvicorn app.main:app --reload

# 5 — open docs
# http://localhost:8000/docs
```

## Adding a new endpoint

1. Create `app/api/v1/endpoints/your_resource.py`
2. Create `app/schemas/your_resource.py`
3. Create `app/services/your_resource_service.py`
4. Register the router in `app/api/v1/router.py`

## Running tests
```bash
pytest tests/
```
