from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from .models import AuditEntry, Author, DmCreate, DmMessage, Post, PostCreate


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
                persona_id TEXT NOT NULL
            );
            """
        )
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

    def list_posts(self, limit: int = 50) -> List[Post]:
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
            INSERT INTO audit_entries (prompt, model_name, output, timestamp, persona_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry.prompt,
                entry.model_name,
                entry.output,
                entry.timestamp.isoformat(),
                str(entry.persona_id),
            ),
        )
        self.connection.commit()

    def list_audit_entries(self, limit: int = 200) -> List[AuditEntry]:
        cursor = self.connection.execute(
            """
            SELECT prompt, model_name, output, timestamp, persona_id
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
            )
            for row in rows
        ]
        return list(reversed(entries))

    @staticmethod
    def _thread_key(user_a: UUID, user_b: UUID) -> Tuple[UUID, UUID]:
        ordered = sorted([user_a, user_b], key=lambda value: str(value))
        return ordered[0], ordered[1]
