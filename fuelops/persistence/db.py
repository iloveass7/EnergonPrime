"""Durable participant-owned records: recommendations, allocation intents, alerts, audit.

Neon PostgreSQL via DATABASE_URL (asyncpg); without it a local SQLite file keeps the demo
running (reported as degraded). Tables are created on startup; Alembic owns migrations later.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, index=True)
    created_tick: Mapped[int] = mapped_column(Integer)
    station_id: Mapped[str] = mapped_column(String(64), index=True)
    fuel_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)
    decided_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IntentRow(Base):
    __tablename__ = "allocation_intents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rec_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    leg: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    body_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), index=True
    )  # SUBMITTING|ACCEPTED|REJECTED|RECONCILING|NEEDS_REVIEW
    sim_allocation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("key", "opened_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(String(64))
    epoch: Mapped[int] = mapped_column(Integer)
    opened_tick: Mapped[int] = mapped_column(Integer)
    closed_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        if self.is_sqlite:
            Path("data").mkdir(exist_ok=True)
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if not self.is_sqlite:
            kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.healthy = False

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.healthy = True

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("select 1"))
            self.healthy = True
        except Exception:  # noqa: BLE001 - any failure means unhealthy
            self.healthy = False
        return self.healthy

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()

    # ------------------------------------------------------------ helpers
    async def audit(
        self,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        *,
        epoch: int | None = None,
        tick: int | None = None,
    ) -> None:
        async with self.session() as s:
            s.add(
                AuditRow(
                    ts=time.time(),
                    kind=kind,
                    actor=actor,
                    epoch=epoch,
                    tick=tick,
                    payload=payload,
                )
            )

    async def upsert_recommendations(self, recs: list[dict[str, Any]]) -> None:
        if not recs:
            return
        async with self.session() as s:
            existing = set(
                (
                    await s.execute(
                        select(RecommendationRow.id).where(
                            RecommendationRow.id.in_([x["id"] for x in recs])
                        )
                    )
                ).scalars()
            )
            for rec in recs:
                if rec["id"] in existing:
                    await s.execute(
                        update(RecommendationRow)
                        .where(RecommendationRow.id == rec["id"])
                        .values(status=rec["status"], payload=rec)
                    )
                else:
                    s.add(
                        RecommendationRow(
                            id=rec["id"],
                            epoch=rec["epoch"],
                            created_tick=rec["created_tick"],
                            station_id=rec["station_id"],
                            fuel_type=rec["fuel_type"],
                            status=rec["status"],
                            payload=rec,
                            created_at=time.time(),
                        )
                    )

    async def set_rec_status(self, ids: list[str], status: str) -> None:
        if not ids:
            return
        async with self.session() as s:
            rows = (
                (
                    await s.execute(
                        select(RecommendationRow).where(
                            RecommendationRow.id.in_(ids),
                            RecommendationRow.status == "PROPOSED",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = status
                row.payload = {**row.payload, "status": status}

    async def get_rec(self, rec_id: str) -> RecommendationRow | None:
        async with self.session() as s:
            return await s.get(RecommendationRow, rec_id)
