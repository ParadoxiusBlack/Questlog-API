from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import (
    Base,
    InventoryEntry,
    Item,
    NPC,
    Player,
    Quest,
    User,
    create_session_factory,
    seed_defaults,
    verify_password,
)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./questlog.db")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "development-secret-key-change-me-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
_request_history: dict[str, deque[float]] = defaultdict(deque)
_request_lock = Lock()


class ErrorResponse(BaseModel):
    error: str
    message: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PlayerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PlayerResponse(PlayerCreate):
    id: int

    model_config = {"from_attributes": True}


class QuestBase(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    description: str = ""
    status: str = Field(default="available", min_length=1, max_length=50)


class QuestUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    status: str | None = Field(default=None, min_length=1, max_length=50)


class QuestResponse(QuestBase):
    id: int
    created_by_id: int

    model_config = {"from_attributes": True}


class NPCBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=120)


class NPCUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, min_length=1, max_length=120)


class NPCResponse(NPCBase):
    id: int

    model_config = {"from_attributes": True}


class ItemBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rarity: str = Field(min_length=1, max_length=50)
    description: str = ""


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rarity: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = None


class ItemResponse(ItemBase):
    id: int

    model_config = {"from_attributes": True}


class InventoryEntryInput(BaseModel):
    item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class InventoryUpdate(BaseModel):
    entries: list[InventoryEntryInput]


class InventoryEntryResponse(BaseModel):
    item_id: int
    item_name: str
    rarity: str
    quantity: int


class APIError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        self.status_code = status_code
        self.error = error
        self.message = message


def create_access_token(subject: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_db(request: Request):
    session_local = request.app.state.SessionLocal
    db = session_local()
    try:
        yield db
    finally:
        db.close()


def get_current_user(db: Annotated[Session, Depends(get_db)], token: Annotated[str, Depends(oauth2_scheme)]) -> User:
    credentials_error = APIError(status.HTTP_401_UNAUTHORIZED, "Unauthorized", "Invalid or expired token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise credentials_error from exc

    username = payload.get("sub")
    if not username:
        raise credentials_error

    user = db.query(User).filter_by(username=username).first()
    if user is None or not user.is_active:
        raise credentials_error
    return user


def not_found(db: Session, model, item_id: int, label: str):
    item = db.get(model, item_id)
    if item is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "NotFound", f"{label} {item_id} not found")
    return item


def handle_integrity_error(db: Session, entity_name: str, field_name: str) -> None:
    db.rollback()
    raise APIError(status.HTTP_409_CONFLICT, "Conflict", f"{entity_name} with that {field_name} already exists")


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        return await call_next(request)

    key = request.client.host if request.client else "anonymous"
    now = time.time()

    with _request_lock:
        recent_requests = _request_history[key]
        while recent_requests and now - recent_requests[0] >= RATE_WINDOW_SECONDS:
            recent_requests.popleft()
        if len(recent_requests) >= RATE_LIMIT:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "RateLimitExceeded",
                    "message": f"Too many requests. Limit is {RATE_LIMIT} per {RATE_WINDOW_SECONDS} seconds.",
                },
            )
        recent_requests.append(now)

    return await call_next(request)


def inventory_payload(player: Player) -> list[InventoryEntryResponse]:
    return [
        InventoryEntryResponse(
            item_id=entry.item.id,
            item_name=entry.item.name,
            rarity=entry.item.rarity,
            quantity=entry.quantity,
        )
        for entry in player.inventory_entries
    ]


def create_app(database_url: str = DATABASE_URL) -> FastAPI:
    app = FastAPI(
        title="QuestLog API",
        description="RESTful backend for quests, NPCs, items, and player inventory.",
        version="1.0.0",
    )
    app.middleware("http")(rate_limit_middleware)

    engine, session_local = create_session_factory(database_url)
    Base.metadata.create_all(bind=engine)
    with session_local() as session:
        seed_defaults(session)

    app.state.engine = engine
    app.state.SessionLocal = session_local

    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(error=exc.error, message=exc.message).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(error="ValidationError", message=str(exc)).model_dump(),
        )

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    @app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
    def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: Annotated[Session, Depends(get_db)]):
        user = db.query(User).filter_by(username=form_data.username).first()
        if user is None or not verify_password(form_data.password, user.hashed_password):
            raise APIError(status.HTTP_401_UNAUTHORIZED, "Unauthorized", "Incorrect username or password")
        return TokenResponse(access_token=create_access_token(user.username))

    @app.get("/players", response_model=list[PlayerResponse], tags=["players"])
    def list_players(skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
        return db.query(Player).offset(skip).limit(limit).all()

    @app.post("/players", response_model=PlayerResponse, status_code=status.HTTP_201_CREATED, tags=["players"])
    def create_player(
        payload: PlayerCreate,
        db: Session = Depends(get_db),
        _: User = Depends(get_current_user),
    ):
        player = Player(name=payload.name)
        db.add(player)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "Player", "name")
        db.refresh(player)
        return player

    @app.get("/players/{player_id}", response_model=PlayerResponse, tags=["players"])
    def get_player(player_id: int, db: Session = Depends(get_db)):
        return not_found(db, Player, player_id, "Player")

    @app.get("/quests", response_model=list[QuestResponse], tags=["quests"])
    def list_quests(status_filter: str | None = None, skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
        query = db.query(Quest)
        if status_filter:
            query = query.filter(Quest.status == status_filter)
        return query.order_by(Quest.id).offset(skip).limit(limit).all()

    @app.post("/quests", response_model=QuestResponse, status_code=status.HTTP_201_CREATED, tags=["quests"])
    def create_quest(
        payload: QuestBase,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        quest = Quest(**payload.model_dump(), created_by_id=current_user.id)
        db.add(quest)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "Quest", "title")
        db.refresh(quest)
        return quest

    @app.get("/quests/{quest_id}", response_model=QuestResponse, tags=["quests"])
    def get_quest(quest_id: int, db: Session = Depends(get_db)):
        return not_found(db, Quest, quest_id, "Quest")

    @app.put("/quests/{quest_id}", response_model=QuestResponse, tags=["quests"])
    def update_quest(
        quest_id: int,
        payload: QuestUpdate,
        db: Session = Depends(get_db),
        _: User = Depends(get_current_user),
    ):
        quest = not_found(db, Quest, quest_id, "Quest")
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(quest, field, value)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "Quest", "title")
        db.refresh(quest)
        return quest

    @app.delete("/quests/{quest_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["quests"])
    def delete_quest(quest_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        quest = not_found(db, Quest, quest_id, "Quest")
        db.delete(quest)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/npcs", response_model=list[NPCResponse], tags=["npcs"])
    def list_npcs(role: str | None = None, skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
        query = db.query(NPC)
        if role:
            query = query.filter(NPC.role == role)
        return query.order_by(NPC.id).offset(skip).limit(limit).all()

    @app.post("/npcs", response_model=NPCResponse, status_code=status.HTTP_201_CREATED, tags=["npcs"])
    def create_npc(payload: NPCBase, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        npc = NPC(**payload.model_dump())
        db.add(npc)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "NPC", "name")
        db.refresh(npc)
        return npc

    @app.get("/npcs/{npc_id}", response_model=NPCResponse, tags=["npcs"])
    def get_npc(npc_id: int, db: Session = Depends(get_db)):
        return not_found(db, NPC, npc_id, "NPC")

    @app.put("/npcs/{npc_id}", response_model=NPCResponse, tags=["npcs"])
    def update_npc(
        npc_id: int,
        payload: NPCUpdate,
        db: Session = Depends(get_db),
        _: User = Depends(get_current_user),
    ):
        npc = not_found(db, NPC, npc_id, "NPC")
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(npc, field, value)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "NPC", "name")
        db.refresh(npc)
        return npc

    @app.delete("/npcs/{npc_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["npcs"])
    def delete_npc(npc_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        npc = not_found(db, NPC, npc_id, "NPC")
        db.delete(npc)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/items", response_model=list[ItemResponse], tags=["items"])
    def list_items(rarity: str | None = None, skip: int = 0, limit: int = 20, db: Session = Depends(get_db)):
        query = db.query(Item)
        if rarity:
            query = query.filter(Item.rarity == rarity)
        return query.order_by(Item.id).offset(skip).limit(limit).all()

    @app.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED, tags=["items"])
    def create_item(payload: ItemBase, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        item = Item(**payload.model_dump())
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "Item", "name")
        db.refresh(item)
        return item

    @app.get("/items/{item_id}", response_model=ItemResponse, tags=["items"])
    def get_item(item_id: int, db: Session = Depends(get_db)):
        return not_found(db, Item, item_id, "Item")

    @app.put("/items/{item_id}", response_model=ItemResponse, tags=["items"])
    def update_item(
        item_id: int,
        payload: ItemUpdate,
        db: Session = Depends(get_db),
        _: User = Depends(get_current_user),
    ):
        item = not_found(db, Item, item_id, "Item")
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(item, field, value)
        try:
            db.commit()
        except IntegrityError:
            handle_integrity_error(db, "Item", "name")
        db.refresh(item)
        return item

    @app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["items"])
    def delete_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        item = not_found(db, Item, item_id, "Item")
        db.delete(item)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/players/{player_id}/inventory", response_model=list[InventoryEntryResponse], tags=["inventory"])
    def get_inventory(player_id: int, db: Session = Depends(get_db)):
        player = not_found(db, Player, player_id, "Player")
        return inventory_payload(player)

    @app.put("/players/{player_id}/inventory", response_model=list[InventoryEntryResponse], tags=["inventory"])
    def replace_inventory(
        player_id: int,
        payload: InventoryUpdate,
        db: Session = Depends(get_db),
        _: User = Depends(get_current_user),
    ):
        player = not_found(db, Player, player_id, "Player")
        requested_item_ids = {entry.item_id for entry in payload.entries}
        if payload.entries:
            existing_items = {item.id: item for item in db.query(Item).filter(Item.id.in_(requested_item_ids)).all()}
            missing_ids = sorted(requested_item_ids - set(existing_items))
            if missing_ids:
                raise APIError(status.HTTP_404_NOT_FOUND, "NotFound", f"Item {missing_ids[0]} not found")

        for entry in list(player.inventory_entries):
            db.delete(entry)
        db.flush()

        quantities: dict[int, int] = defaultdict(int)
        for entry in payload.entries:
            quantities[entry.item_id] += entry.quantity

        for item_id, quantity in quantities.items():
            db.add(InventoryEntry(player_id=player.id, item_id=item_id, quantity=quantity))

        db.commit()
        db.refresh(player)
        return inventory_payload(player)

    return app


app = create_app()
