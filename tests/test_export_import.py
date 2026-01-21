import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import AuditEntry, Author, DmCreate, PostCreate
from app.store import InMemoryStore


class ExportImportTest(unittest.TestCase):
    def test_export_import_roundtrip(self) -> None:
        store = InMemoryStore()
        author_a = Author(id=uuid4(), handle="alpha", display_name="Alpha", type="bot")
        author_b = Author(id=uuid4(), handle="bravo", display_name="Bravo", type="human")
        store.add_author(author_a)
        store.add_author(author_b)

        post_root = store.create_post(
            PostCreate(author_id=author_a.id, content="root post", reply_to=None, quote_of=None)
        )
        post_reply = store.create_post(
            PostCreate(author_id=author_b.id, content="reply", reply_to=post_root.id, quote_of=None)
        )
        store.toggle_like(post_root.id, author_b.id)
        dm_message = store.create_dm(
            DmCreate(sender_id=author_b.id, recipient_id=author_a.id, content="hello dm")
        )

        base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        store.posts[post_root.id] = post_root.model_copy(update={"created_at": base_time})
        store.posts[post_reply.id] = post_reply.model_copy(
            update={"created_at": base_time + timedelta(minutes=5)}
        )
        thread_key = store._thread_key(dm_message.sender_id, dm_message.recipient_id)
        store.dms[thread_key][0] = dm_message.model_copy(
            update={"created_at": base_time + timedelta(minutes=10)}
        )

        store.add_audit_entry(
            AuditEntry(
                prompt="prompt",
                model_name="model",
                output="root post",
                timestamp=base_time + timedelta(minutes=1),
                persona_id=author_a.id,
                post_id=post_root.id,
            )
        )
        store.add_audit_entry(
            AuditEntry(
                prompt="dm prompt",
                model_name="model",
                output="hello dm",
                timestamp=base_time + timedelta(minutes=11),
                persona_id=author_a.id,
                dm_id=dm_message.id,
            )
        )

        exported = store.export_dataset()
        imported_store = InMemoryStore()
        imported_store.import_dataset(exported)
        roundtrip = imported_store.export_dataset()

        self.assertEqual(exported["metadata"]["counts"], roundtrip["metadata"]["counts"])
        self.assertEqual(exported["authors"], roundtrip["authors"])
        self.assertEqual(exported["posts"], roundtrip["posts"])
        self.assertEqual(exported["dms"], roundtrip["dms"])
        self.assertEqual(exported["likes"], roundtrip["likes"])
        self.assertEqual(exported["audit_entries"], roundtrip["audit_entries"])

        original_timeline_ids = [post.id for post in store.list_posts(limit=10)]
        imported_timeline_ids = [post.id for post in imported_store.list_posts(limit=10)]
        self.assertEqual(original_timeline_ids, imported_timeline_ids)


if __name__ == "__main__":
    unittest.main()
