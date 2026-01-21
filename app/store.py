from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from .models import AuditEntry, Author, DmCreate, DmMessage, Post, PostCreate


class InMemoryStore:
    def __init__(self) -> None:
        self.authors: Dict[UUID, Author] = {}
        self.posts: Dict[UUID, Post] = {}
        self.dms: Dict[Tuple[UUID, UUID], List[DmMessage]] = defaultdict(list)
        self.likes: Dict[UUID, set[UUID]] = defaultdict(set)
        self.audit_entries: List[AuditEntry] = []

    def add_author(self, author: Author) -> None:
        self.authors[author.id] = author

    def get_author(self, author_id: UUID) -> Optional[Author]:
        return self.authors.get(author_id)

    def list_authors(self) -> List[Author]:
        return list(self.authors.values())

    def create_post(self, payload: PostCreate) -> Post:
        post_id = uuid4()
        created_at = datetime.now(timezone.utc)
        post = Post(
            id=post_id,
            author_id=payload.author_id,
            content=payload.content,
            reply_to=payload.reply_to,
            quote_of=payload.quote_of,
            created_at=created_at,
        )
        self.posts[post_id] = post
        return post

    def list_posts(self, limit: int = 50) -> List[Post]:
        return sorted(self.posts.values(), key=lambda post: post.created_at, reverse=True)[:limit]

    def has_post(self, post_id: UUID) -> bool:
        return post_id in self.posts

    def create_dm(self, payload: DmCreate) -> DmMessage:
        message = DmMessage(
            id=uuid4(),
            sender_id=payload.sender_id,
            recipient_id=payload.recipient_id,
            content=payload.content,
            created_at=datetime.now(timezone.utc),
        )
        thread_key = self._thread_key(payload.sender_id, payload.recipient_id)
        self.dms[thread_key].append(message)
        return message

    def list_dm_thread(self, user_a: UUID, user_b: UUID, limit: int = 50) -> List[DmMessage]:
        thread_key = self._thread_key(user_a, user_b)
        return self.dms.get(thread_key, [])[-limit:]

    def list_dm_threads(self) -> List[List[DmMessage]]:
        return list(self.dms.values())

    def toggle_like(self, post_id: UUID, author_id: UUID) -> int:
        likes = self.likes[post_id]
        if author_id in likes:
            likes.remove(author_id)
        else:
            likes.add(author_id)
        return len(likes)

    def add_audit_entry(self, entry: AuditEntry) -> None:
        self.audit_entries.append(entry)

    def list_audit_entries(self, limit: int = 200) -> List[AuditEntry]:
        return list(self.audit_entries)[-limit:]

    @staticmethod
    def _thread_key(user_a: UUID, user_b: UUID) -> Tuple[UUID, UUID]:
        ordered = sorted([user_a, user_b], key=lambda value: str(value))
        return ordered[0], ordered[1]
