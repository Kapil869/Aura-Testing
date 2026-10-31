"""ORM models. Timestamps are stored as naive UTC."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    environments: Mapped[list["Environment"]] = relationship(back_populates="user")
    saved_requests: Mapped[list["SavedRequest"]] = relationship(back_populates="user")
    test_runs: Mapped[list["TestRun"]] = relationship(back_populates="user")
    perf_runs: Mapped[list["PerfRun"]] = relationship(back_populates="user")
    ai_analyses: Mapped[list["AIAnalysis"]] = relationship(back_populates="user")


class Environment(Base):
    __tablename__ = "environments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    base_url: Mapped[str] = mapped_column(String(2048))
    description: Mapped[str] = mapped_column(String(500), default="")
    allow_external: Mapped[bool] = mapped_column(Boolean, default=False)
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    requests: Mapped[list["SavedRequest"]] = relationship(back_populates="environment")
    user: Mapped[User] = relationship(back_populates="environments")


class SavedRequest(Base):
    __tablename__ = "saved_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(10))
    url: Mapped[str] = mapped_column(String(2048))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    body_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout: Mapped[float] = mapped_column(Float, default=10.0)
    auth_type: Mapped[str] = mapped_column(String(20), default="none")
    auth_username: Mapped[str] = mapped_column(String(200), default="")
    auth_header_name: Mapped[str] = mapped_column(String(100), default="X-API-Key")
    auth_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    environment: Mapped[Environment | None] = relationship(back_populates="requests")
    user: Mapped[User] = relationship(back_populates="saved_requests")


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False  # tell pytest this is not a test class
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    suite: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="queued")
    target_url: Mapped[str] = mapped_column(String(2048), default="")
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id", ondelete="SET NULL"), nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    retries_used: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    output_tail: Mapped[str] = mapped_column(Text, default="")

    results: Mapped[list["TestResult"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="TestResult.id")
    user: Mapped[User] = relationship(back_populates="test_runs")


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    nodeid: Mapped[str] = mapped_column(String(500), index=True)
    name: Mapped[str] = mapped_column(String(300))
    classname: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(12), index=True)  # passed|failed|error|skipped
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[TestRun] = relationship(back_populates="results")


Index("ix_results_node_run", TestResult.nodeid, TestResult.run_id)


class PerfRun(Base):
    __tablename__ = "perf_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_url: Mapped[str] = mapped_column(String(2048))
    method: Mapped[str] = mapped_column(String(10))
    total_requests: Mapped[int] = mapped_column(Integer)
    concurrency: Mapped[int] = mapped_column(Integer)
    timeout: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    throughput_rps: Mapped[float] = mapped_column(Float, default=0.0)
    error_rate: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[User] = relationship(back_populates="perf_runs")


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(60))  # "rules" or model name
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    run_id: Mapped[int | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[User] = relationship(back_populates="ai_analyses")


Index("uq_environment_user_name", Environment.user_id, Environment.name, unique=True)
Index("ix_test_runs_user_created", TestRun.user_id, TestRun.created_at)
Index("ix_perf_runs_user_created", PerfRun.user_id, PerfRun.created_at)
Index("ix_ai_analyses_user_created", AIAnalysis.user_id, AIAnalysis.created_at)
