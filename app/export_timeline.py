from __future__ import annotations

import argparse
import csv
import json
import sys

from .store_factory import build_store


def _timeline(limit: int) -> list[dict]:
    store = build_store()
    posts = sorted(store.list_posts(limit=limit), key=lambda post: (post.created_at, str(post.id)))
    timeline: list[dict] = []
    for post in posts:
        author = store.get_author(post.author_id)
        timeline.append(
            {
                "id": str(post.id),
                "author_handle": author.handle if author else "unknown",
                "content": post.content,
                "created_at": post.created_at.isoformat(),
                "reply_to": str(post.reply_to) if post.reply_to else None,
                "quote_of": str(post.quote_of) if post.quote_of else None,
            }
        )
    return timeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a shareable timeline in JSON or CSV.")
    parser.add_argument("--limit", type=int, default=200, help="Number of posts to include.")
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output format.",
    )
    args = parser.parse_args()

    timeline = _timeline(args.limit)
    if args.format == "csv":
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=["id", "author_handle", "content", "created_at", "reply_to", "quote_of"],
        )
        writer.writeheader()
        writer.writerows(timeline)
    else:
        json.dump(timeline, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
