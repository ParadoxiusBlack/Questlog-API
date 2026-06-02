from __future__ import annotations

import hashlib
import hmac
import os

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    quests: Mapped[list["Quest"]] = relationship(back_populates="created_by")


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    inventory_entries: Mapped[list["InventoryEntry"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class Quest(Base):
    __tablename__ = "quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="available", nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_by: Mapped[User] = relationship(back_populates="quests")


class NPC(Base):
    __tablename__ = "npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(120), nullable=False)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    rarity: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    inventory_entries: Mapped[list["InventoryEntry"]] = relationship(back_populates="item")


class InventoryEntry(Base):
    __tablename__ = "inventory_entries"
    __table_args__ = (UniqueConstraint("player_id", "item_id", name="uq_inventory_player_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    player: Mapped[Player] = relationship(back_populates="inventory_entries")
    item: Mapped[Item] = relationship(back_populates="inventory_entries")


def hash_password(password: str, salt: str | None = None) -> str:
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 390000)
    return f"{salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, stored_password: str) -> bool:
    salt, stored_digest = stored_password.split("$", maxsplit=1)
    computed_digest = hash_password(password, salt).split("$", maxsplit=1)[1]
    return hmac.compare_digest(computed_digest, stored_digest)


def create_session_factory(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return engine, session_local


def seed_defaults(session: Session) -> None:
    demo_username = os.getenv("QUESTLOG_DEMO_USERNAME", "demo")
    demo_password = os.getenv("QUESTLOG_DEMO_PASSWORD", "questlog-demo")

    if session.query(User).filter_by(username=demo_username).first() is None:
        session.add(User(username=demo_username, hashed_password=hash_password(demo_password)))

    if session.query(Player).filter_by(name="Hero of Oakvale").first() is None:
        session.add(Player(name="Hero of Oakvale"))

    session.commit()
