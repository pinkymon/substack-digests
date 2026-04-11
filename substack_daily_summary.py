#!/usr/bin/env python3
"""
Daily Substack Inbox Summarizer for @pinkymon

Fetches all subscribed newsletters via Substack's public profile API,
retrieves recent posts from each RSS feed, and summarizes them with Claude.

No authentication required.

Requirements:
    pip install anthropic feedparser requests python-dotenv
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import anthropic
import argparse
import io
import re
import sys
from datetime import datetime, timezone, timedelta

# Force UTF-8 output on Windows to handle emoji/CJK in newsletter names
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import feedparser
    import requests
except ImportError:
    print("Missing dependencies: pip install feedparser requests", file=sys.stderr)
    sys.exit(1)

SUBSTACK_HANDLE = "pinkymon"
OUTPUT_DIR = Path("./substack_summaries")
LOOKBACK_HOURS = 24
# Max chars of post content to send to Claude (keeps token cost reasonable)
MAX_CONTENT_CHARS = 2000


def get_subscriptions(handle: str) -> list[dict]:
    """Fetch list of newsletter subscriptions from the public Substack profile."""
    url = f"https://substack.com/api/v1/user/{handle}/public_profile"
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    subs = []
    for sub in data.get("subscriptions", []):
        pub = sub.get("publication", {})
        subdomain = pub.get("subdomain")
        name = pub.get("name", subdomain)
        if subdomain:
            subs.append({
                "name": name,
                "subdomain": subdomain,
                "feed_url": f"https://{subdomain}.substack.com/feed",
            })
    return subs


def strip_html(html: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_recent_posts_from_feed(sub: dict, cutoff: datetime) -> list[dict]:
    """Fetch posts published after cutoff from a single newsletter's RSS feed."""
    try:
        feed = feedparser.parse(sub["feed_url"])
    except Exception as ex:
        print(f"  Warning: failed to fetch {sub['name']}: {ex}", file=sys.stderr)
        return []

    posts = []
    for entry in feed.entries:
        # Parse date
        pub_date = None
        date_str = entry.get("published") or entry.get("updated")
        if date_str:
            try:
                pub_date = parsedate_to_datetime(date_str)
                if pub_date < cutoff:
                    continue  # Entries are newest-first; can stop here
            except Exception:
                pass  # Include post if we can't parse the date

        # Extract content
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = strip_html(entry.content[0].value)
        elif entry.get("summary"):
            content = strip_html(entry.summary)

        posts.append({
            "title": entry.get("title", "Untitled"),
            "publication": sub["name"],
            "url": entry.get("link", ""),
            "published": date_str or "Unknown date",
            "content": content[:MAX_CONTENT_CHARS],
        })

    return posts


def fetch_all_recent_posts(handle: str, lookback_hours: int) -> list[dict]:
    """Fetch recent posts from all subscribed newsletters."""
    print(f"Fetching subscriptions for @{handle}...")
    subscriptions = get_subscriptions(handle)
    print(f"Found {len(subscriptions)} newsletter subscriptions.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    all_posts = []

    for sub in subscriptions:
        print(f"  Checking: {sub['name']}...", end="", flush=True)
        posts = fetch_recent_posts_from_feed(sub, cutoff)
        if posts:
            print(f" {len(posts)} new post(s)")
            all_posts.extend(posts)
        else:
            print(" (no new posts)")

    return all_posts


def summarize_posts(posts: list[dict]) -> str:
    """Summarize inbox posts using Claude."""
    if not posts:
        return "No new posts found in your Substack inbox in the last 24 hours."

    client = anthropic.Anthropic()

    # Build prompt content, grouped by publication
    by_pub: dict[str, list[dict]] = {}
    for post in posts:
        by_pub.setdefault(post["publication"], []).append(post)

    posts_text = ""
    for pub_name, pub_posts in by_pub.items():
        posts_text += f"\n## {pub_name}\n"
        for post in pub_posts:
            posts_text += f"\n### {post['title']}\n"
            posts_text += f"URL: {post['url']}\n"
            posts_text += f"Published: {post['published']}\n\n"
            if post["content"]:
                posts_text += post["content"]
                if len(post["content"]) == MAX_CONTENT_CHARS:
                    posts_text += "\n[... content truncated ...]"
            posts_text += "\n\n---\n"

    today = datetime.now().strftime("%B %d, %Y")

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=(
            "你是一位簡潔的電子報摘要助手，請全程使用台灣繁體中文回覆。"
            "為讀者建立清晰的每日摘要，幫助他們快速掌握最新資訊。"
            "針對每篇文章：提供 2–4 條重點摘要。"
            "在整份摘要最上方加上一行簡短的「TL;DR」總結。"
            "在摘要最末尾加上「📝 編輯評語與建議」段落，"
            "針對今日內容整體提出你的觀察、看法與閱讀建議（150–250 字）。"
            "使用乾淨的 Markdown 格式，力求簡潔易讀。"
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"請為 {today} 建立每日 Substack 電子報摘要（請用台灣繁體中文）。"
                    f"共有來自 {len(by_pub)} 份電子報的 {len(posts)} 篇新文章：\n\n"
                    f"{posts_text}"
                ),
            }
        ],
    )

    return response.content[0].text


def save_summary(summary: str, post_count: int, pub_count: int) -> Path:
    """Save the summary as a dated markdown file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    file_path = OUTPUT_DIR / f"digest_{today_str}.md"

    today_dt = datetime.now()
    header = (
        f"# Substack 每日精華 — {today_dt.strftime('%Y年%m月%d日')}\n"
        f"*共 {post_count} 篇文章，來自 {pub_count} 份電子報 · @{SUBSTACK_HANDLE} · 由 Claude 生成*\n\n"
    )
    file_path.write_text(header + summary, encoding="utf-8")
    return file_path


def push_to_github(file_path: Path) -> None:
    """Commit and push the digest file to GitHub."""
    import subprocess
    repo_root = Path(__file__).parent.resolve()
    rel_path = file_path.resolve().relative_to(repo_root)
    today_str = datetime.now().strftime("%Y-%m-%d")

    cmds = [
        ["git", "add", str(rel_path)],
        ["git", "commit", "-m", f"digest: add {today_str} summary"],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Warning: `{' '.join(cmd)}` failed: {result.stderr.strip()}", file=sys.stderr)
            return
    print(f"Pushed to GitHub: {rel_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Summarize your Substack subscriptions using Claude"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=LOOKBACK_HOURS,
        help=f"Look back N hours for posts (default: {LOOKBACK_HOURS})",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print summary to stdout only, don't save to file",
    )
    args = parser.parse_args()

    posts = fetch_all_recent_posts(SUBSTACK_HANDLE, args.hours)

    if not posts:
        print(f"\nNo new posts in the last {args.hours} hours. Try --hours 48 or --hours 72.")
        return

    pub_count = len({p["publication"] for p in posts})
    print(f"\n{len(posts)} new post(s) from {pub_count} newsletter(s). Summarizing with Claude...")

    summary = summarize_posts(posts)

    print("\n" + "=" * 60)
    print(summary)
    print("=" * 60)

    if not args.no_save:
        saved_path = save_summary(summary, len(posts), pub_count)
        print(f"\nSaved to: {saved_path}")
        push_to_github(saved_path)


if __name__ == "__main__":
    main()
