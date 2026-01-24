from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID, uuid4

from .models import AuditEntry, Author, DmCreate, DmMessage, MemoryEntry, Post, PostCreate


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS authors (
                id TEXT PRIMARY KEY,
                handle TEXT NOT NULL,
                display_name TEXT NOT NULL,
                type TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                author_id TEXT NOT NULL,
                content TEXT NOT NULL,
                reply_to TEXT,
                quote_of TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dms (
                id TEXT PRIMARY KEY,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                thread_user_a TEXT NOT NULL,
                thread_user_b TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS likes (
                post_id TEXT NOT NULL,
                author_id TEXT NOT NULL,
                PRIMARY KEY (post_id, author_id)
            );
            CREATE TABLE IF NOT EXISTS audit_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                model_name TEXT NOT NULL,
                output TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                post_id TEXT,
                dm_id TEXT
            );
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL,
                salience REAL NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL
            );
            """
        )
        self.connection.commit()
        self._ensure_audit_columns()

    def _ensure_audit_columns(self) -> None:
        cursor = self.connection.execute("PRAGMA table_info(audit_entries)")
        existing = {row["name"] for row in cursor.fetchall()}
        migrations = []
        if "post_id" not in existing:
            migrations.append("ALTER TABLE audit_entries ADD COLUMN post_id TEXT")
        if "dm_id" not in existing:
            migrations.append("ALTER TABLE audit_entries ADD COLUMN dm_id TEXT")
        for statement in migrations:
            self.connection.execute(statement)
        if migrations:
            self.connection.commit()

    def add_author(self, author: Author) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO authors (id, handle, display_name, type)
            VALUES (?, ?, ?, ?)
            """,
            (str(author.id), author.handle, author.display_name, author.type),
        )
        self.connection.commit()

    def get_author(self, author_id: UUID) -> Optional[Author]:
        cursor = self.connection.execute(
            "SELECT id, handle, display_name, type FROM authors WHERE id = ?",
            (str(author_id),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return Author(
            id=UUID(row["id"]),
            handle=row["handle"],
            display_name=row["display_name"],
            type=row["type"],
        )

    def list_authors(self) -> List[Author]:
        cursor = self.connection.execute(
            "SELECT id, handle, display_name, type FROM authors ORDER BY handle"
        )
        return [
            Author(
                id=UUID(row["id"]),
                handle=row["handle"],
                display_name=row["display_name"],
                type=row["type"],
            )
            for row in cursor.fetchall()
        ]

    def get_post(self, post_id: UUID) -> Optional[Post]:
        cursor = self.connection.execute(
            """
            SELECT id, author_id, content, reply_to, quote_of, created_at
            FROM posts
            WHERE id = ?
            """,
            (str(post_id),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return Post(
            id=UUID(row["id"]),
            author_id=UUID(row["author_id"]),
            content=row["content"],
            reply_to=UUID(row["reply_to"]) if row["reply_to"] else None,
            quote_of=UUID(row["quote_of"]) if row["quote_of"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create_post(self, payload: PostCreate) -> Post:
        post_id = uuid4()
        created_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """
            INSERT INTO posts (id, author_id, content, reply_to, quote_of, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(post_id),
                str(payload.author_id),
                payload.content,
                str(payload.reply_to) if payload.reply_to else None,
                str(payload.quote_of) if payload.quote_of else None,
                created_at,
            ),
        )
        self.connection.commit()
        return Post(
            id=post_id,
            author_id=payload.author_id,
            content=payload.content,
            reply_to=payload.reply_to,
            quote_of=payload.quote_of,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_posts(self, limit: int = 50, author_id: UUID | None = None) -> List[Post]:
        if author_id is not None:
            cursor = self.connection.execute(
                """
                SELECT id, author_id, content, reply_to, quote_of, created_at
                FROM posts
                WHERE author_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(author_id), limit),
            )
        else:
            cursor = self.connection.execute(
                """
                SELECT id, author_id, content, reply_to, quote_of, created_at
                FROM posts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        posts = []
        for row in cursor.fetchall():
            posts.append(
                Post(
                    id=UUID(row["id"]),
                    author_id=UUID(row["author_id"]),
                    content=row["content"],
                    reply_to=UUID(row["reply_to"]) if row["reply_to"] else None,
                    quote_of=UUID(row["quote_of"]) if row["quote_of"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return posts

    def count_posts(self) -> int:
        cursor = self.connection.execute("SELECT COUNT(*) FROM posts")
        return cursor.fetchone()[0]

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
        cursor = self.connection.execute(
            """
            SELECT id, author_id, content, reply_to, quote_of, created_at
            FROM posts
            """
        )
        posts = [
            Post(
                id=UUID(row["id"]),
                author_id=UUID(row["author_id"]),
                content=row["content"],
                reply_to=UUID(row["reply_to"]) if row["reply_to"] else None,
                quote_of=UUID(row["quote_of"]) if row["quote_of"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in cursor.fetchall()
        ]
        reply_counts: Dict[UUID, int] = {}
        quote_counts: Dict[UUID, int] = {}
        likes_counts: Dict[UUID, int] = {}

        reply_cursor = self.connection.execute(
            """
            SELECT reply_to, COUNT(*) AS count
            FROM posts
            WHERE reply_to IS NOT NULL
            GROUP BY reply_to
            """
        )
        for row in reply_cursor.fetchall():
            reply_counts[UUID(row["reply_to"])] = int(row["count"])

        quote_cursor = self.connection.execute(
            """
            SELECT quote_of, COUNT(*) AS count
            FROM posts
            WHERE quote_of IS NOT NULL
            GROUP BY quote_of
            """
        )
        for row in quote_cursor.fetchall():
            quote_counts[UUID(row["quote_of"])] = int(row["count"])

        likes_cursor = self.connection.execute(
            """
            SELECT post_id, COUNT(*) AS count
            FROM likes
            GROUP BY post_id
            """
        )
        for row in likes_cursor.fetchall():
            likes_counts[UUID(row["post_id"])] = int(row["count"])

        now = datetime.now(timezone.utc)
        window_seconds = recency_window_hours * 3600

        def score(post: Post) -> float:
            age_seconds = (now - post.created_at).total_seconds()
            if window_seconds > 0:
                recency_score = recency_weight * max(0.0, 1.0 - age_seconds / window_seconds)
            else:
                recency_score = 0.0
            like_score = likes_counts.get(post.id, 0) * like_weight
            reply_score = reply_counts.get(post.id, 0) * reply_weight
            quote_score = quote_counts.get(post.id, 0) * quote_weight
            return recency_score + like_score + reply_score + quote_score

        ranked = sorted(posts, key=lambda post: (score(post), post.created_at), reverse=True)
        return ranked[:limit]

    def has_post(self, post_id: UUID) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM posts WHERE id = ?",
            (str(post_id),),
        )
        return cursor.fetchone() is not None

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
        cursor = self.connection.execute(
            """
            SELECT id, author_id, content, reply_to, quote_of, created_at
            FROM posts
            WHERE reply_to = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (str(post_id), limit),
        )
        replies = []
        for row in cursor.fetchall():
            replies.append(
                Post(
                    id=UUID(row["id"]),
                    author_id=UUID(row["author_id"]),
                    content=row["content"],
                    reply_to=UUID(row["reply_to"]) if row["reply_to"] else None,
                    quote_of=UUID(row["quote_of"]) if row["quote_of"] else None,
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return replies

    def create_dm(self, payload: DmCreate) -> DmMessage:
        message_id = uuid4()
        created_at = datetime.now(timezone.utc).isoformat()
        thread_user_a, thread_user_b = self._thread_key(payload.sender_id, payload.recipient_id)
        self.connection.execute(
            """
            INSERT INTO dms (
                id, sender_id, recipient_id, thread_user_a, thread_user_b, content, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(message_id),
                str(payload.sender_id),
                str(payload.recipient_id),
                str(thread_user_a),
                str(thread_user_b),
                payload.content,
                created_at,
            ),
        )
        self.connection.commit()
        return DmMessage(
            id=message_id,
            sender_id=payload.sender_id,
            recipient_id=payload.recipient_id,
            content=payload.content,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_dm_thread(self, user_a: UUID, user_b: UUID, limit: int = 50) -> List[DmMessage]:
        thread_user_a, thread_user_b = self._thread_key(user_a, user_b)
        cursor = self.connection.execute(
            """
            SELECT id, sender_id, recipient_id, content, created_at
            FROM dms
            WHERE thread_user_a = ? AND thread_user_b = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(thread_user_a), str(thread_user_b), limit),
        )
        messages = [
            DmMessage(
                id=UUID(row["id"]),
                sender_id=UUID(row["sender_id"]),
                recipient_id=UUID(row["recipient_id"]),
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in cursor.fetchall()
        ]
        return list(reversed(messages))

    def list_dm_threads(self) -> List[List[DmMessage]]:
        cursor = self.connection.execute(
            """
            SELECT id, sender_id, recipient_id, thread_user_a, thread_user_b, content, created_at
            FROM dms
            ORDER BY thread_user_a, thread_user_b, created_at ASC
            """
        )
        threads: List[List[DmMessage]] = []
        current_key: Optional[Tuple[str, str]] = None
        current_messages: List[DmMessage] = []
        for row in cursor.fetchall():
            key = (row["thread_user_a"], row["thread_user_b"])
            if current_key != key:
                if current_messages:
                    threads.append(current_messages)
                current_messages = []
                current_key = key
            current_messages.append(
                DmMessage(
                    id=UUID(row["id"]),
                    sender_id=UUID(row["sender_id"]),
                    recipient_id=UUID(row["recipient_id"]),
                    content=row["content"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        if current_messages:
            threads.append(current_messages)
        return threads

    def get_dm_thread_preview(self, user_a: UUID, user_b: UUID) -> Optional[DmMessage]:
        """Get last message in thread for preview."""
        thread = self.list_dm_thread(user_a, user_b, limit=1)
        return thread[-1] if thread else None

    def count_dm_threads_with_metadata(self, human_id: UUID) -> List[dict]:
        """Get all DM threads with metadata for sidebar."""
        threads = []
        all_threads = self.list_dm_threads()

        for thread_messages in all_threads:
            if not thread_messages:
                continue

            first_msg = thread_messages[0]
            if human_id not in [first_msg.sender_id, first_msg.recipient_id]:
                continue

            bot_id = (
                first_msg.recipient_id
                if first_msg.sender_id == human_id
                else first_msg.sender_id
            )
            bot = self.get_author(bot_id)

            if not bot or bot.type != "bot":
                continue

            last_msg = thread_messages[-1]

            threads.append({
                "bot": bot,
                "last_message": last_msg,
                "message_count": len(thread_messages),
                "unread": last_msg.sender_id == bot_id,  # Simple heuristic
            })

        threads.sort(key=lambda t: t["last_message"].created_at, reverse=True)
        return threads

    def toggle_like(self, post_id: UUID, author_id: UUID) -> int:
        cursor = self.connection.execute(
            "SELECT 1 FROM likes WHERE post_id = ? AND author_id = ?",
            (str(post_id), str(author_id)),
        )
        if cursor.fetchone():
            self.connection.execute(
                "DELETE FROM likes WHERE post_id = ? AND author_id = ?",
                (str(post_id), str(author_id)),
            )
        else:
            self.connection.execute(
                "INSERT INTO likes (post_id, author_id) VALUES (?, ?)",
                (str(post_id), str(author_id)),
            )
        self.connection.commit()
        count_cursor = self.connection.execute(
            "SELECT COUNT(*) AS count FROM likes WHERE post_id = ?",
            (str(post_id),),
        )
        return int(count_cursor.fetchone()["count"])

    def has_like(self, post_id: UUID, author_id: UUID) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM likes WHERE post_id = ? AND author_id = ?",
            (str(post_id), str(author_id)),
        )
        return cursor.fetchone() is not None

    def add_audit_entry(self, entry: AuditEntry) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_entries (prompt, model_name, output, timestamp, persona_id, post_id, dm_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.prompt,
                entry.model_name,
                entry.output,
                entry.timestamp.isoformat(),
                str(entry.persona_id),
                str(entry.post_id) if entry.post_id else None,
                str(entry.dm_id) if entry.dm_id else None,
            ),
        )
        self.connection.commit()

    def list_audit_entries(self, limit: int = 200) -> List[AuditEntry]:
        cursor = self.connection.execute(
            """
            SELECT prompt, model_name, output, timestamp, persona_id, post_id, dm_id
            FROM audit_entries
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        entries = [
            AuditEntry(
                prompt=row["prompt"],
                model_name=row["model_name"],
                output=row["output"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                persona_id=UUID(row["persona_id"]),
                post_id=UUID(row["post_id"]) if row["post_id"] else None,
                dm_id=UUID(row["dm_id"]) if row["dm_id"] else None,
            )
            for row in rows
        ]
        return list(reversed(entries))

    def add_memory(self, entry: MemoryEntry) -> None:
        self.connection.execute(
            """
            INSERT INTO memories (persona_id, content, tags, salience, created_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(entry.persona_id),
                entry.content,
                json.dumps(entry.tags, ensure_ascii=False),
                entry.salience,
                entry.created_at.isoformat(),
                entry.source,
            ),
        )
        self.connection.commit()

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
        cursor = self.connection.execute(
            """
            SELECT persona_id, content, tags, salience, created_at, source
            FROM memories
            WHERE persona_id = ?
            """,
            (str(persona_id),),
        )
        memories = [
            MemoryEntry(
                persona_id=UUID(row["persona_id"]),
                content=row["content"],
                tags=json.loads(row["tags"]) if row["tags"] else [],
                salience=float(row["salience"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                source=row["source"],
            )
            for row in cursor.fetchall()
        ]
        now = datetime.now(timezone.utc)
        window_seconds = recency_window_hours * 3600

        def score(entry: MemoryEntry) -> float:
            age_seconds = (now - entry.created_at).total_seconds()
            if window_seconds > 0:
                recency_score = recency_weight * max(0.0, 1.0 - age_seconds / window_seconds)
            else:
                recency_score = 0.0
            return recency_score + (entry.salience * salience_weight)

        ranked = sorted(memories, key=lambda entry: (score(entry), entry.created_at), reverse=True)
        return ranked[:limit]

    def prune_memories(
        self,
        persona_id: UUID,
        *,
        max_entries: int | None = None,
        ttl_hours: float | None = None,
        recency_weight: float = 1.0,
        salience_weight: float = 1.0,
        recency_window_hours: float = 168.0,
    ) -> int:
        removed = 0
        if ttl_hours is not None and ttl_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
            cursor = self.connection.execute(
                """
                DELETE FROM memories
                WHERE persona_id = ? AND created_at < ?
                """,
                (str(persona_id), cutoff.isoformat()),
            )
            removed += cursor.rowcount or 0

        if max_entries is not None and max_entries >= 0:
            cursor = self.connection.execute(
                """
                SELECT id, persona_id, content, tags, salience, created_at, source
                FROM memories
                WHERE persona_id = ?
                """,
                (str(persona_id),),
            )
            rows = cursor.fetchall()
            if len(rows) > max_entries:
                now = datetime.now(timezone.utc)
                window_seconds = recency_window_hours * 3600
                scored = []
                for row in rows:
                    entry = MemoryEntry(
                        persona_id=UUID(row["persona_id"]),
                        content=row["content"],
                        tags=json.loads(row["tags"]) if row["tags"] else [],
                        salience=float(row["salience"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        source=row["source"],
                    )
                    age_seconds = (now - entry.created_at).total_seconds()
                    if window_seconds > 0:
                        recency_score = recency_weight * max(0.0, 1.0 - age_seconds / window_seconds)
                    else:
                        recency_score = 0.0
                    score = recency_score + (entry.salience * salience_weight)
                    scored.append((row["id"], score, entry.created_at))
                scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
                keep_ids = {entry_id for entry_id, _, _ in scored[:max_entries]}
                delete_ids = [entry_id for entry_id, _, _ in scored if entry_id not in keep_ids]
                if delete_ids:
                    placeholders = ",".join(["?"] * len(delete_ids))
                    cursor = self.connection.execute(
                        f"DELETE FROM memories WHERE id IN ({placeholders})",
                        delete_ids,
                    )
                    removed += cursor.rowcount or 0

        if removed:
            self.connection.commit()
        return removed

    def export_dataset(self) -> dict:
        authors_cursor = self.connection.execute(
            "SELECT id, handle, display_name, type FROM authors ORDER BY handle"
        )
        authors = [
            {
                "id": row["id"],
                "handle": row["handle"],
                "display_name": row["display_name"],
                "type": row["type"],
            }
            for row in authors_cursor.fetchall()
        ]
        posts_cursor = self.connection.execute(
            """
            SELECT id, author_id, content, reply_to, quote_of, created_at
            FROM posts
            ORDER BY created_at ASC, id ASC
            """
        )
        posts = [
            {
                "id": row["id"],
                "author_id": row["author_id"],
                "content": row["content"],
                "reply_to": row["reply_to"],
                "quote_of": row["quote_of"],
                "created_at": row["created_at"],
            }
            for row in posts_cursor.fetchall()
        ]
        dms_cursor = self.connection.execute(
            """
            SELECT id, sender_id, recipient_id, content, created_at
            FROM dms
            ORDER BY created_at ASC, id ASC
            """
        )
        dms = [
            {
                "id": row["id"],
                "sender_id": row["sender_id"],
                "recipient_id": row["recipient_id"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in dms_cursor.fetchall()
        ]
        likes_cursor = self.connection.execute(
            """
            SELECT post_id, author_id
            FROM likes
            ORDER BY post_id ASC, author_id ASC
            """
        )
        likes = [
            {"post_id": row["post_id"], "author_id": row["author_id"]}
            for row in likes_cursor.fetchall()
        ]
        audit_cursor = self.connection.execute(
            """
            SELECT prompt, model_name, output, timestamp, persona_id, post_id, dm_id
            FROM audit_entries
            ORDER BY timestamp ASC, id ASC
            """
        )
        audit_entries = [
            {
                "prompt": row["prompt"],
                "model_name": row["model_name"],
                "output": row["output"],
                "timestamp": row["timestamp"],
                "persona_id": row["persona_id"],
                "post_id": row["post_id"],
                "dm_id": row["dm_id"],
            }
            for row in audit_cursor.fetchall()
        ]
        memories_cursor = self.connection.execute(
            """
            SELECT persona_id, content, tags, salience, created_at, source
            FROM memories
            ORDER BY created_at ASC, id ASC
            """
        )
        memories = [
            {
                "persona_id": row["persona_id"],
                "content": row["content"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "salience": row["salience"],
                "created_at": row["created_at"],
                "source": row["source"],
            }
            for row in memories_cursor.fetchall()
        ]
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
            "authors": authors,
            "posts": posts,
            "dms": dms,
            "likes": likes,
            "audit_entries": audit_entries,
            "memories": memories,
        }

    def import_dataset(self, payload: dict) -> None:
        cursor = self.connection.cursor()
        cursor.executescript(
            """
            DELETE FROM likes;
            DELETE FROM dms;
            DELETE FROM posts;
            DELETE FROM authors;
            DELETE FROM audit_entries;
            DELETE FROM memories;
            """
        )
        for author in payload.get("authors", []):
            cursor.execute(
                """
                INSERT INTO authors (id, handle, display_name, type)
                VALUES (?, ?, ?, ?)
                """,
                (
                    author["id"],
                    author["handle"],
                    author["display_name"],
                    author["type"],
                ),
            )
        for post in payload.get("posts", []):
            cursor.execute(
                """
                INSERT INTO posts (id, author_id, content, reply_to, quote_of, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    post["id"],
                    post["author_id"],
                    post["content"],
                    post.get("reply_to"),
                    post.get("quote_of"),
                    post["created_at"],
                ),
            )
        for message in payload.get("dms", []):
            thread_user_a, thread_user_b = self._thread_key(
                UUID(message["sender_id"]), UUID(message["recipient_id"])
            )
            cursor.execute(
                """
                INSERT INTO dms (
                    id, sender_id, recipient_id, thread_user_a, thread_user_b, content, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    message["sender_id"],
                    message["recipient_id"],
                    str(thread_user_a),
                    str(thread_user_b),
                    message["content"],
                    message["created_at"],
                ),
            )
        for like in payload.get("likes", []):
            cursor.execute(
                "INSERT INTO likes (post_id, author_id) VALUES (?, ?)",
                (like["post_id"], like["author_id"]),
            )
        for entry in payload.get("audit_entries", []):
            cursor.execute(
                """
                INSERT INTO audit_entries (prompt, model_name, output, timestamp, persona_id, post_id, dm_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["prompt"],
                    entry["model_name"],
                    entry["output"],
                    entry["timestamp"],
                    entry["persona_id"],
                    entry.get("post_id"),
                    entry.get("dm_id"),
                ),
            )
        for entry in payload.get("memories", []):
            cursor.execute(
                """
                INSERT INTO memories (persona_id, content, tags, salience, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["persona_id"],
                    entry["content"],
                    json.dumps(entry.get("tags", []), ensure_ascii=False),
                    entry.get("salience", 0.0),
                    entry["created_at"],
                    entry.get("source", "unknown"),
                ),
            )
        self.connection.commit()

    @staticmethod
    def _thread_key(user_a: UUID, user_b: UUID) -> Tuple[UUID, UUID]:
        ordered = sorted([user_a, user_b], key=lambda value: str(value))
        return ordered[0], ordered[1]
