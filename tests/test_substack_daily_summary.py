"""Tests for substack_daily_summary.py"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests as req

import substack_daily_summary as sds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_entry(title, published, summary="Test content", has_content=False, content_val=None):
    """Build a MagicMock that mimics a feedparser entry."""
    data = {
        "title": title,
        "published": published,
        "updated": None,
        "link": f"https://example.com/{title.replace(' ', '-')}",
        "summary": summary,
    }
    entry = MagicMock()
    entry.get.side_effect = lambda k, d="": data.get(k, d)
    entry.summary = summary
    if has_content and content_val:
        content_item = MagicMock()
        content_item.value = content_val
        entry.content = [content_item]
    else:
        entry.content = None
    return entry


def recent_date_str(hours_ago=1):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_strips_tags(self):
        assert sds.strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_strips_nested_tags(self):
        assert sds.strip_html("<div><span>text</span></div>") == "text"

    def test_decodes_amp(self):
        assert sds.strip_html("Tom &amp; Jerry") == "Tom & Jerry"

    def test_decodes_lt_gt(self):
        assert sds.strip_html("&lt;tag&gt;") == "<tag>"

    def test_decodes_quot(self):
        assert sds.strip_html("&quot;quoted&quot;") == '"quoted"'

    def test_decodes_nbsp(self):
        result = sds.strip_html("hello&nbsp;world")
        assert result == "hello world"

    def test_collapses_whitespace(self):
        assert sds.strip_html("a   b\n\tc") == "a b c"

    def test_empty_string(self):
        assert sds.strip_html("") == ""

    def test_strips_only_tags_leaves_text(self):
        assert sds.strip_html("plain text") == "plain text"


# ---------------------------------------------------------------------------
# get_subscriptions
# ---------------------------------------------------------------------------

class TestGetSubscriptions:
    def _mock_response(self, json_data):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_happy_path_returns_subs(self):
        payload = {
            "subscriptions": [
                {"publication": {"subdomain": "testnews", "name": "Test News"}},
                {"publication": {"subdomain": "another", "name": "Another"}},
            ]
        }
        with patch("substack_daily_summary.requests.get", return_value=self._mock_response(payload)):
            result = sds.get_subscriptions("testuser")

        assert len(result) == 2
        assert result[0] == {
            "name": "Test News",
            "subdomain": "testnews",
            "feed_url": "https://testnews.substack.com/feed",
        }

    def test_missing_subscriptions_key_returns_empty(self):
        with patch("substack_daily_summary.requests.get", return_value=self._mock_response({})):
            result = sds.get_subscriptions("testuser")
        assert result == []

    def test_skips_entry_without_subdomain(self):
        payload = {
            "subscriptions": [
                {"publication": {"name": "No Subdomain Here"}},
            ]
        }
        with patch("substack_daily_summary.requests.get", return_value=self._mock_response(payload)):
            result = sds.get_subscriptions("testuser")
        assert result == []

    def test_uses_subdomain_as_name_fallback(self):
        payload = {
            "subscriptions": [
                {"publication": {"subdomain": "mysub"}},  # no name
            ]
        }
        with patch("substack_daily_summary.requests.get", return_value=self._mock_response(payload)):
            result = sds.get_subscriptions("testuser")
        assert result[0]["name"] == "mysub"

    def test_http_error_propagates(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("404")
        with patch("substack_daily_summary.requests.get", return_value=mock_resp):
            with pytest.raises(req.HTTPError):
                sds.get_subscriptions("testuser")


# ---------------------------------------------------------------------------
# fetch_recent_posts_from_feed
# ---------------------------------------------------------------------------

class TestFetchRecentPostsFromFeed:
    SUB = {"name": "Test NL", "subdomain": "test", "feed_url": "https://test.substack.com/feed"}

    def test_returns_recent_post(self):
        entry = make_entry("New Post", recent_date_str(1))
        mock_feed = MagicMock()
        mock_feed.entries = [entry]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("substack_daily_summary.feedparser.parse", return_value=mock_feed):
            result = sds.fetch_recent_posts_from_feed(self.SUB, cutoff)

        assert len(result) == 1
        assert result[0]["title"] == "New Post"
        assert result[0]["publication"] == "Test NL"

    def test_skips_old_post(self):
        entry = make_entry("Old Post", recent_date_str(48))
        mock_feed = MagicMock()
        mock_feed.entries = [entry]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        with patch("substack_daily_summary.feedparser.parse", return_value=mock_feed):
            result = sds.fetch_recent_posts_from_feed(self.SUB, cutoff)

        assert result == []

    def test_feedparser_exception_returns_empty(self):
        with patch("substack_daily_summary.feedparser.parse", side_effect=Exception("Connection error")):
            result = sds.fetch_recent_posts_from_feed(self.SUB, datetime.now(timezone.utc))
        assert result == []

    def test_truncates_content_to_max_chars(self):
        long_text = "x" * (sds.MAX_CONTENT_CHARS + 500)
        entry = make_entry("Long Post", recent_date_str(1), summary=long_text)
        mock_feed = MagicMock()
        mock_feed.entries = [entry]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("substack_daily_summary.feedparser.parse", return_value=mock_feed):
            result = sds.fetch_recent_posts_from_feed(self.SUB, cutoff)

        assert len(result[0]["content"]) <= sds.MAX_CONTENT_CHARS

    def test_uses_content_field_when_present(self):
        entry = make_entry("Post", recent_date_str(1), has_content=True, content_val="<p>Rich content</p>")
        mock_feed = MagicMock()
        mock_feed.entries = [entry]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("substack_daily_summary.feedparser.parse", return_value=mock_feed):
            result = sds.fetch_recent_posts_from_feed(self.SUB, cutoff)

        assert "Rich content" in result[0]["content"]

    def test_includes_post_with_unparseable_date(self):
        entry = make_entry("Mystery Post", "not-a-date")
        mock_feed = MagicMock()
        mock_feed.entries = [entry]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("substack_daily_summary.feedparser.parse", return_value=mock_feed):
            result = sds.fetch_recent_posts_from_feed(self.SUB, cutoff)

        # Can't parse date → included by default
        assert len(result) == 1
        assert result[0]["title"] == "Mystery Post"


# ---------------------------------------------------------------------------
# fetch_all_recent_posts
# ---------------------------------------------------------------------------

class TestFetchAllRecentPosts:
    def test_aggregates_posts_from_multiple_subs(self):
        subs = [
            {"name": "A", "subdomain": "a", "feed_url": "https://a.substack.com/feed"},
            {"name": "B", "subdomain": "b", "feed_url": "https://b.substack.com/feed"},
        ]
        posts_a = [{"title": "Post A", "publication": "A", "url": "", "published": "", "content": ""}]
        posts_b = [
            {"title": "Post B1", "publication": "B", "url": "", "published": "", "content": ""},
            {"title": "Post B2", "publication": "B", "url": "", "published": "", "content": ""},
        ]

        with patch("substack_daily_summary.get_subscriptions", return_value=subs):
            with patch("substack_daily_summary.fetch_recent_posts_from_feed", side_effect=[posts_a, posts_b]):
                result = sds.fetch_all_recent_posts("testuser", 24)

        assert len(result) == 3

    def test_no_subscriptions_returns_empty(self):
        with patch("substack_daily_summary.get_subscriptions", return_value=[]):
            result = sds.fetch_all_recent_posts("testuser", 24)
        assert result == []

    def test_passes_correct_handle(self):
        with patch("substack_daily_summary.get_subscriptions", return_value=[]) as mock_get:
            sds.fetch_all_recent_posts("myhandle", 24)
        mock_get.assert_called_once_with("myhandle")


# ---------------------------------------------------------------------------
# summarize_posts
# ---------------------------------------------------------------------------

class TestSummarizePosts:
    def test_empty_posts_returns_no_new_posts_message(self):
        result = sds.summarize_posts([])
        assert "No new posts" in result

    def test_calls_claude_and_returns_text(self):
        posts = [
            {
                "title": "Test Post",
                "publication": "Test NL",
                "url": "https://example.com",
                "published": "Wed, 09 Apr 2026 10:00:00 +0000",
                "content": "Some content here.",
            }
        ]
        mock_text = MagicMock()
        mock_text.text = "## Digest\n- Key point"
        mock_message = MagicMock()
        mock_message.content = [mock_text]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("substack_daily_summary.anthropic.Anthropic", return_value=mock_client):
            result = sds.summarize_posts(posts)

        assert "Digest" in result
        mock_client.messages.create.assert_called_once()

    def test_groups_posts_by_publication(self):
        posts = [
            {"title": "A1", "publication": "Pub A", "url": "", "published": "", "content": ""},
            {"title": "A2", "publication": "Pub A", "url": "", "published": "", "content": ""},
            {"title": "B1", "publication": "Pub B", "url": "", "published": "", "content": ""},
        ]
        mock_text = MagicMock()
        mock_text.text = "summary"
        mock_message = MagicMock()
        mock_message.content = [mock_text]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("substack_daily_summary.anthropic.Anthropic", return_value=mock_client):
            sds.summarize_posts(posts)

        call_kwargs = mock_client.messages.create.call_args
        user_content = call_kwargs.kwargs["messages"][0]["content"]
        # Prompt should mention 3 posts from 2 newsletters
        assert "3 new post" in user_content
        assert "2 newsletter" in user_content


# ---------------------------------------------------------------------------
# save_summary
# ---------------------------------------------------------------------------

class TestSaveSummary:
    def test_creates_file_with_correct_name(self, tmp_path):
        with patch("substack_daily_summary.OUTPUT_DIR", tmp_path):
            saved = sds.save_summary("Test summary", 3, 2)

        today_str = datetime.now().strftime("%Y-%m-%d")
        assert saved.name == f"digest_{today_str}.md"

    def test_file_contains_summary_text(self, tmp_path):
        with patch("substack_daily_summary.OUTPUT_DIR", tmp_path):
            saved = sds.save_summary("My unique digest content", 5, 3)

        content = saved.read_text(encoding="utf-8")
        assert "My unique digest content" in content

    def test_file_contains_header(self, tmp_path):
        with patch("substack_daily_summary.OUTPUT_DIR", tmp_path):
            saved = sds.save_summary("content", 1, 1)

        content = saved.read_text(encoding="utf-8")
        assert "Substack Daily Digest" in content

    def test_creates_nested_output_dir(self, tmp_path):
        nested = tmp_path / "a" / "b"
        with patch("substack_daily_summary.OUTPUT_DIR", nested):
            sds.save_summary("content", 1, 1)
        assert nested.exists()

    def test_returns_path_object(self, tmp_path):
        with patch("substack_daily_summary.OUTPUT_DIR", tmp_path):
            result = sds.save_summary("content", 1, 1)
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_no_posts_prints_message_and_returns(self, capsys):
        with patch("substack_daily_summary.fetch_all_recent_posts", return_value=[]):
            with patch("sys.argv", ["script.py"]):
                sds.main()

        out = capsys.readouterr().out
        assert "No new posts" in out

    def test_saves_by_default(self, tmp_path):
        posts = [{"title": "T", "publication": "P", "url": "", "published": "", "content": ""}]
        with patch("substack_daily_summary.fetch_all_recent_posts", return_value=posts):
            with patch("substack_daily_summary.summarize_posts", return_value="Summary"):
                with patch("substack_daily_summary.save_summary", return_value=tmp_path / "d.md") as mock_save:
                    with patch("sys.argv", ["script.py"]):
                        sds.main()
        mock_save.assert_called_once()

    def test_no_save_flag_skips_file_write(self):
        posts = [{"title": "T", "publication": "P", "url": "", "published": "", "content": ""}]
        with patch("substack_daily_summary.fetch_all_recent_posts", return_value=posts):
            with patch("substack_daily_summary.summarize_posts", return_value="Summary"):
                with patch("substack_daily_summary.save_summary") as mock_save:
                    with patch("sys.argv", ["script.py", "--no-save"]):
                        sds.main()
        mock_save.assert_not_called()

    def test_hours_arg_passed_to_fetch(self):
        with patch("substack_daily_summary.fetch_all_recent_posts", return_value=[]) as mock_fetch:
            with patch("sys.argv", ["script.py", "--hours", "48"]):
                sds.main()
        mock_fetch.assert_called_once_with(sds.SUBSTACK_HANDLE, 48)
