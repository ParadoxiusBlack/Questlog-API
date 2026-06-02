# Questlog-API

RESTful backend for managing quests, NPCs, items, and player progress.

## Features

- Resource-oriented REST endpoints for quests, NPCs, items, players, and player inventory
- CRUD operations mapped to HTTP verbs
- Stateless JWT authentication for protected write operations
- SQLite + SQLAlchemy persistence layer with Alembic migrations
- Pagination and filtering on collection endpoints
- Consistent JSON error responses
- Auto-generated OpenAPI documentation via FastAPI
- Docker-ready runtime configuration
- Pytest integration tests for key flows

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs on `http://127.0.0.1:8000` with interactive docs at `/docs`.

### Demo credentials

- Username: `demo`
- Password: `questlog-demo`

### Common commands

```bash
pytest
alembic upgrade head
docker build -t questlog-api .
```

## Example endpoints

- `POST /auth/token`
- `GET /quests`
- `POST /quests`
- `GET /quests/{id}`
- `PUT /quests/{id}`
- `DELETE /quests/{id}`
- `GET /npcs`
- `GET /items`
- `GET /players/{id}/inventory`
- `PUT /players/{id}/inventory`
