from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from .models import Author, DmCreate, DmMessage, Post, PostCreate


class InMemoryStore:
    def __init__(self) -> None:
        self.authors: Dict[UUID, Author] = {}
        self.posts: Dict[UUID, Post] = {}
        self.dms: Dict[Tuple[UUID, UUID], List[DmMessage]] = defaultdict(list)
        self.likes: Dict[UUID, set[UUID]] = defaultdict(set)

    def add_author(self, author: Author) -> None:
        self.authors[author.id] = author

    def get_author(self, author_id: UUID) -> Optional[Author]:
        return self.authors.get(author_id)

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

    def toggle_like(self, post_id: UUID, author_id: UUID) -> int:
        likes = self.likes[post_id]
        if author_id in likes:
            likes.remove(author_id)
        else:
            likes.add(author_id)
        return len(likes)

    @staticmethod
    def _thread_key(user_a: UUID, user_b: UUID) -> Tuple[UUID, UUID]:
        ordered = sorted([user_a, user_b], key=lambda value: str(value))
        return ordered[0], ordered[1]
