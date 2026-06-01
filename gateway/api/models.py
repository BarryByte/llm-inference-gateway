"""Pydantic request and response models for all API endpoints."""
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Priority(str, Enum):
    high = "high"
    normal = "normal"
    low = "low"


class ProcessRequest(BaseModel):
    user_id: str
    prompt_id: str
    text: str
    priority: Priority = Priority.normal
    callback_url: Optional[str] = None


class ProcessResponse(BaseModel):
    user_id: str
    prompt_id: str
    status: str                        # queued | processing | completed | failed
    cached: Optional[bool] = None
    response: Optional[str] = None
    processing_time_ms: Optional[int] = None
    error: Optional[str] = None
    retry_count: Optional[int] = None


class JobStatus(BaseModel):
    prompt_id: str
    status: str
    response: Optional[str] = None
    model_tier: Optional[str] = None
    cached: Optional[bool] = None
    attempts: int = 0
    last_error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    components: dict
