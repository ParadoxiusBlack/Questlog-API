from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    return TestClient(create_app(database_url))


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        data={"username": "demo", "password": "questlog-demo"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": "Bearer " + token}


def test_openapi_and_health_endpoints_exist(tmp_path: Path):
    client = make_client(tmp_path)

    health = client.get("/health")
    docs = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert docs.status_code == 200
    assert docs.json()["info"]["title"] == "QuestLog API"


def test_quest_crud_and_filtering_require_auth(tmp_path: Path):
    client = make_client(tmp_path)

    unauthorized = client.post("/quests", json={"title": "Save Oakvale", "description": "Defeat bandits", "status": "open"})
    assert unauthorized.status_code == 401

    headers = auth_headers(client)
    created = client.post(
        "/quests",
        json={"title": "Save Oakvale", "description": "Defeat bandits", "status": "open"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    quest_id = created.json()["id"]

    listed = client.get("/quests", params={"status_filter": "open", "skip": 0, "limit": 10})
    assert listed.status_code == 200
    assert [quest["title"] for quest in listed.json()] == ["Save Oakvale"]

    updated = client.put(f"/quests/{quest_id}", json={"status": "completed"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"


def test_inventory_replace_returns_item_details(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)

    item = client.post(
        "/items",
        json={"name": "Ancient Key", "rarity": "rare", "description": "Opens forgotten gates."},
        headers=headers,
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]

    inventory = client.put(
        "/players/1/inventory",
        json={"entries": [{"item_id": item_id, "quantity": 2}]},
        headers=headers,
    )
    assert inventory.status_code == 200, inventory.text
    assert inventory.json() == [
        {"item_id": item_id, "item_name": "Ancient Key", "rarity": "rare", "quantity": 2}
    ]


def test_not_found_errors_are_consistent(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/quests/999")

    assert response.status_code == 404
    assert response.json() == {"error": "NotFound", "message": "Quest 999 not found"}
