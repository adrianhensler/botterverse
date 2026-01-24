from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID, uuid4

from .models import AuditEntry, Author, DmCreate, DmMessage, MemoryEntry, Post, PostCreate


class InMemoryStore:
    def __init__(self) -> None:
        self.authors: Dict[UUID, Author] = {}
        self.posts: Dict[UUID, Post] = {}
        self.dms: Dict[Tuple[UUID, UUID], List[DmMessage]] = defaultdict(list)
        self.likes: Dict[UUID, set[UUID]] = defaultdict(set)
        self.audit_entries: List[AuditEntry] = []
        self.memories: List[MemoryEntry] = []

    def add_author(self, author: Author) -> None:
        self.authors[author.id] = author

    def get_author(self, author_id: UUID) -> Optional[Author]:
        return self.authors.get(author_id)

    def list_authors(self) -> List[Author]:
        return list(self.authors.values())

    def get_post(self, post_id: UUID) -> Optional[Post]:
        return self.posts.get(post_id)

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

    def list_posts(self, limit: int = 50, author_id: UUID | None = None) -> List[Post]:
        posts = self.posts.values()
        if author_id is not None:
            posts = [p for p in posts if p.author_id == author_id]
        return sorted(posts, key=lambda post: post.created_at, reverse=True)[:limit]

    def count_posts(self) -> int:
        return len(self.posts)

    def list_posts_ranked(
        self,
        limit: int = 50,
        *,
        like_weight: float = 0.2,
        reply_weight: float = 0.6,
        quote_weight: float = 0.4,
        recency_weight: float = 1.0,
        recency_window_hours: float = 24.0,
    ) -> List[Post]:
        posts = list(self.posts.values())
        reply_counts: Dict[UUID, int] = defaultdict(int)
        quote_counts: Dict[UUID, int] = defaultdict(int)
        for post in posts:
            if post.reply_to is not None:
                reply_counts[post.reply_to] += 1
            if post.quote_of is not None:
                quote_counts[post.quote_of] += 1

        now = datetime.now(timezone.utc)
        window_seconds = recency_window_hours * 3600

        def score(post: Post) -> float:
            age_seconds = (now - post.created_at).total_seconds()
            if window_seconds > 0:
                recency_score = recency_weight * max(0.0, 1.0 - age_seconds / window_seconds)
            else:
                recency_score = 0.0
            like_score = len(self.likes[post.id]) * like_weight
            reply_score = reply_counts[post.id] * reply_weight
            quote_score = quote_counts[post.id] * quote_weight
            return recency_score + like_score + reply_score + quote_score

        ranked = sorted(posts, key=lambda post: (score(post), post.created_at), reverse=True)
        return ranked[:limit]

    def has_post(self, post_id: UUID) -> bool:
        return post_id in self.posts

    def get_reply_context(self, post_id: UUID) -> Optional[Post]:
        """Get the parent post that this post is replying to."""
        post = self.get_post(post_id)
        if post and post.reply_to:
            return self.get_post(post.reply_to)
        return None

    def get_quote_context(self, post_id: UUID) -> Optional[Post]:
        """Get the quoted post."""
        post = self.get_post(post_id)
        if post and post.quote_of:
            return self.get_post(post.quote_of)
        return None

    def get_reply_chain(self, post_id: UUID, max_depth: int = 10) -> List[Post]:
        """Get full reply chain from root to current post."""
        chain = []
        current_id = post_id
        depth = 0

        while current_id and depth < max_depth:
            post = self.get_post(current_id)
            if not post:
                break
            chain.insert(0, post)  # Prepend to maintain root → leaf order
            current_id = post.reply_to
            depth += 1

        return chain

    def get_replies_to_post(self, post_id: UUID, limit: int = 50) -> List[Post]:
        """Get all direct replies to a post."""
        replies = [p for p in self.posts.values() if p.reply_to == post_id]
        return sorted(replies, key=lambda p: p.created_at)[:limit]

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

    def has_like(self, post_id: UUID, author_id: UUID) -> bool:
        return author_id in self.likes[post_id]

    def add_audit_entry(self, entry: AuditEntry) -> None:
        self.audit_entries.append(entry)

    def list_audit_entries(self, limit: int = 200) -> List[AuditEntry]:
        return list(self.audit_entries)[-limit:]

    def add_memory(self, entry: MemoryEntry) -> None:
        self.memories.append(entry)

    def add_memory_from_post(
        self,
        persona_id: UUID,
        post: Post,
        *,
        tags: Sequence[str] | None = None,
        salience: float = 0.6,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            persona_id=persona_id,
            content=post.content,
            tags=list(tags or ["post"]),
            salience=salience,
            created_at=post.created_at,
            source="post",
        )
        self.add_memory(entry)
        return entry

    def add_memory_from_dm(
        self,
        persona_id: UUID,
        message: DmMessage,
        *,
        tags: Sequence[str] | None = None,
        salience: float = 0.7,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            persona_id=persona_id,
            content=message.content,
            tags=list(tags or ["dm"]),
            salience=salience,
            created_at=message.created_at,
            source="dm",
        )
        self.add_memory(entry)
        return entry

    def add_memory_from_event(
        self,
        persona_id: UUID,
        topic: str,
        *,
        payload: Dict[str, object] | None = None,
        tags: Sequence[str] | None = None,
        salience: float = 0.8,
    ) -> MemoryEntry:
        content = topic
        if payload:
            serialized = json.dumps(payload, default=str, ensure_ascii=False)
            content = f"{topic} (payload: {serialized})"
        entry = MemoryEntry(
            persona_id=persona_id,
            content=content,
            tags=list(tags or ["event"]),
            salience=salience,
            created_at=datetime.now(timezone.utc),
            source="event",
        )
        self.add_memory(entry)
        return entry

    def list_memories_ranked(
        self,
        persona_id: UUID,
        limit: int = 5,
        *,
        recency_weight: float = 1.0,
        salience_weight: float = 1.0,
        recency_window_hours: float = 168.0,
    ) -> List[MemoryEntry]:
        now = datetime.now(timezone.utc)
        window_seconds = recency_window_hours * 3600
        memories = [entry for entry in self.memories if entry.persona_id == persona_id]

        def score(entry: MemoryEntry) -> float:
            age_seconds = (now - entry.created_at).total_seconds()
            if window_seconds > 0:
                recency_score = recency_weight * max(0.0, 1.0 - age_seconds / window_seconds)
            else:
                recency_score = 0.0
            return recency_score + (entry.salience * salience_weight)

        ranked = sorted(memories, key=lambda entry: (score(entry), entry.created_at), reverse=True)
        return ranked[:limit]

    def export_dataset(self) -> dict:
        authors = sorted(self.authors.values(), key=lambda author: author.handle)
        posts = sorted(self.posts.values(), key=lambda post: (post.created_at, str(post.id)))
        dms = sorted(
            [message for thread in self.dms.values() for message in thread],
            key=lambda message: (message.created_at, str(message.id)),
        )
        likes = sorted(
            [
                {"post_id": str(post_id), "author_id": str(author_id)}
                for post_id, author_ids in self.likes.items()
                for author_id in author_ids
            ],
            key=lambda item: (item["post_id"], item["author_id"]),
        )
        audit_entries = sorted(
            self.audit_entries,
            key=lambda entry: (entry.timestamp, str(entry.persona_id), str(entry.post_id or "")),
        )
        memories = sorted(
            self.memories,
            key=lambda entry: (entry.created_at, str(entry.persona_id), entry.source),
        )
        return {
            "metadata": {
                "version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "counts": {
                    "authors": len(authors),
                    "posts": len(posts),
                    "dms": len(dms),
                    "likes": len(likes),
                    "audit_entries": len(audit_entries),
                    "memories": len(memories),
                },
            },
            "authors": [
                {
                    "id": str(author.id),
                    "handle": author.handle,
                    "display_name": author.display_name,
                    "type": author.type,
                }
                for author in authors
            ],
            "posts": [
                {
                    "id": str(post.id),
                    "author_id": str(post.author_id),
                    "content": post.content,
                    "reply_to": str(post.reply_to) if post.reply_to else None,
                    "quote_of": str(post.quote_of) if post.quote_of else None,
                    "created_at": post.created_at.isoformat(),
                }
                for post in posts
            ],
            "dms": [
                {
                    "id": str(message.id),
                    "sender_id": str(message.sender_id),
                    "recipient_id": str(message.recipient_id),
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
                for message in dms
            ],
            "likes": likes,
            "audit_entries": [
                {
                    "prompt": entry.prompt,
                    "model_name": entry.model_name,
                    "output": entry.output,
                    "timestamp": entry.timestamp.isoformat(),
                    "persona_id": str(entry.persona_id),
                    "post_id": str(entry.post_id) if entry.post_id else None,
                    "dm_id": str(entry.dm_id) if entry.dm_id else None,
                }
                for entry in audit_entries
            ],
            "memories": [
                {
                    "persona_id": str(entry.persona_id),
                    "content": entry.content,
                    "tags": entry.tags,
                    "salience": entry.salience,
                    "created_at": entry.created_at.isoformat(),
                    "source": entry.source,
                }
                for entry in memories
            ],
        }

    def import_dataset(self, payload: dict) -> None:
        self.authors.clear()
        self.posts.clear()
        self.dms.clear()
        self.likes.clear()
        self.audit_entries.clear()
        self.memories.clear()

        for author in payload.get("authors", []):
            parsed = Author(
                id=UUID(author["id"]),
                handle=author["handle"],
                display_name=author["display_name"],
                type=author["type"],
            )
            self.authors[parsed.id] = parsed

        for post in payload.get("posts", []):
            parsed = Post(
                id=UUID(post["id"]),
                author_id=UUID(post["author_id"]),
                content=post["content"],
                reply_to=UUID(post["reply_to"]) if post.get("reply_to") else None,
                quote_of=UUID(post["quote_of"]) if post.get("quote_of") else None,
                created_at=datetime.fromisoformat(post["created_at"]),
            )
            self.posts[parsed.id] = parsed

        for message in payload.get("dms", []):
            parsed = DmMessage(
                id=UUID(message["id"]),
                sender_id=UUID(message["sender_id"]),
                recipient_id=UUID(message["recipient_id"]),
                content=message["content"],
                created_at=datetime.fromisoformat(message["created_at"]),
            )
            thread_key = self._thread_key(parsed.sender_id, parsed.recipient_id)
            self.dms[thread_key].append(parsed)

        for like in payload.get("likes", []):
            post_id = UUID(like["post_id"])
            author_id = UUID(like["author_id"])
            self.likes[post_id].add(author_id)

        for entry in payload.get("audit_entries", []):
            parsed = AuditEntry(
                prompt=entry["prompt"],
                model_name=entry["model_name"],
                output=entry["output"],
                timestamp=datetime.fromisoformat(entry["timestamp"]),
                persona_id=UUID(entry["persona_id"]),
                post_id=UUID(entry["post_id"]) if entry.get("post_id") else None,
                dm_id=UUID(entry["dm_id"]) if entry.get("dm_id") else None,
            )
            self.audit_entries.append(parsed)

        for entry in payload.get("memories", []):
            parsed = MemoryEntry(
                persona_id=UUID(entry["persona_id"]),
                content=entry["content"],
                tags=list(entry.get("tags", [])),
                salience=float(entry.get("salience", 0.0)),
                created_at=datetime.fromisoformat(entry["created_at"]),
                source=entry.get("source", "unknown"),
            )
            self.memories.append(parsed)

    @staticmethod
    def _thread_key(user_a: UUID, user_b: UUID) -> Tuple[UUID, UUID]:
        ordered = sorted([user_a, user_b], key=lambda value: str(value))
        return ordered[0], ordered[1]
