from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Author(BaseModel):
    id: UUID
    handle: str
    display_name: str
    type: Literal["human", "bot"]


class PostCreate(BaseModel):
    author_id: UUID
    content: str = Field(min_length=1, max_length=3500)
    reply_to: Optional[UUID] = None
    quote_of: Optional[UUID] = None


class Post(BaseModel):
    id: UUID
    author_id: UUID
    content: str
    reply_to: Optional[UUID]
    quote_of: Optional[UUID]
    created_at: datetime


class DmCreate(BaseModel):
    sender_id: UUID
    recipient_id: UUID
    content: str = Field(min_length=1, max_length=1000)


class DmMessage(BaseModel):
    id: UUID
    sender_id: UUID
    recipient_id: UUID
    content: str
    created_at: datetime


class TimelineEntry(BaseModel):
    post: Post
    author: Author


class AuditEntry(BaseModel):
    prompt: str
    model_name: str
    output: str
    timestamp: datetime
    persona_id: UUID
    post_id: Optional[UUID] = None
    dm_id: Optional[UUID] = None


class AuditEntryWithPost(BaseModel):
    entry: AuditEntry
    post: Optional[Post] = None
    author: Optional[Author] = None


class MemoryEntry(BaseModel):
    persona_id: UUID
    content: str
    tags: list[str]
    salience: float
    created_at: datetime
    source: str
