"""SQLAlchemy ORM models -- maps directly to the data model ."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class Prompt(Base):
    __tablename__ = "prompts"

    prompt_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    priority = Column(String, default="normal")
    status = Column(String, default="pending")
    embedding = Column(Vector(384))
    attempts = Column(Integer, default=0)
    last_error = Column(Text)
    callback_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Response(Base):
    __tablename__ = "responses"

    prompt_id = Column(String, ForeignKey("prompts.prompt_id"), primary_key=True)
    response_text = Column(Text)
    model_tier = Column(String)
    tokens_in = Column(Integer)
    tokens_out = Column(Integer)
    cached_from_prompt_id = Column(String, ForeignKey("prompts.prompt_id"), nullable=True)
    latency_ms = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id = Column(String, primary_key=True)
    embedding = Column(Vector(384))
    response_text = Column(Text)
    hit_count = Column(Integer, default=0)
    last_hit_at = Column(DateTime)
    ttl_expires_at = Column(DateTime)


class DeadLetterEntry(Base):
    __tablename__ = "dlq"

    prompt_id = Column(String, ForeignKey("prompts.prompt_id"), primary_key=True)
    reason_chain = Column(JSONB)
    moved_at = Column(DateTime, default=datetime.now)
