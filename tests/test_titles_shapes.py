"""titles — every branch of the §37 sanitizer pinned before the P3b split.

Characterization net for ``_url_title`` / ``is_unreadable_title`` /
``sanitize_title`` (CONTRACT §37 / §37.1): domain normalisation (userinfo,
port, www.), the ``v=`` video-id rule, noise-segment stripping, the spaced
filesystem-path rule, the long-text clause clip — each edge asserted on its
own so a mutation in any helper flips a test.
"""
import unittest

from act.lib import titles


class UrlTitleTestCase(unittest.TestCase):
    def test_video_id_beats_path(self):
        self.assertEqual(titles._url_title("https://www.youtube.com/watch?v=abc123&t=9"),
                         "youtube.com ▸ abc123")

    def test_video_id_not_first_param(self):
        self.assertEqual(titles._url_title("https://youtu.be/x?feature=share&v=zz"),
                         "youtu.be ▸ zz")

    def test_v_inside_other_param_does_not_count(self):
        # "&v=" / "^v=" only — "tv=" must not match
        self.assertEqual(titles._url_title("https://example.com/a/b?tv=1"),
                         "example.com ▸ b")

    def test_userinfo_and_port_are_stripped(self):
        self.assertEqual(titles._url_title("https://user:pw@www.host.io:8443/docs/guide"),
                         "host.io ▸ guide")

    def test_noise_segments_are_skipped_backwards(self):
        self.assertEqual(titles._url_title("https://site.org/blog/post-1/index.html"),
                         "site.org ▸ post-1")
        self.assertEqual(titles._url_title("https://site.org/blog/post-1/view/p"),
                         "site.org ▸ post-1")

    def test_only_noise_segments_falls_back_to_domain(self):
        self.assertEqual(titles._url_title("https://Site.org/index.html"), "Site.org")

    def test_no_path_no_domain_falls_back_to_url(self):
        # urlparse of "http:///x" has an empty netloc; path "/x" yields "x"
        self.assertEqual(titles._url_title("http:///x"), " ▸ x")
        # nothing at all after the scheme → the raw url
        self.assertEqual(titles._url_title("http://"), "http://")

    def test_unparseable_url_returns_input(self):
        bad = "http://[::1"          # invalid IPv6 literal → urlparse ValueError
        self.assertEqual(titles._url_title(bad), bad)

    def test_trailing_slash_and_empty_segments(self):
        self.assertEqual(titles._url_title("https://a.b//c//"), "a.b ▸ c")


class UnreadableTitleTestCase(unittest.TestCase):
    def test_non_str_and_blank_are_false(self):
        self.assertFalse(titles.is_unreadable_title(None))
        self.assertFalse(titles.is_unreadable_title(12))
        self.assertFalse(titles.is_unreadable_title("   \n "))

    def test_url_and_plain_path(self):
        self.assertTrue(titles.is_unreadable_title("https://x.y/z"))
        self.assertTrue(titles.is_unreadable_title("/Users/z/a.pdf"))
        self.assertTrue(titles.is_unreadable_title("~/Downloads/a.pdf"))

    def test_spaced_path_needs_structure_in_first_segment(self):
        self.assertTrue(titles.is_unreadable_title("/Users/z/My Files/a.pdf"))
        # ~3 天完成 A/B 测试: first token "~3" has no slash after char 0
        self.assertFalse(titles.is_unreadable_title("~3 天完成 A/B 测试"))
        # first token structured but fewer than two slashes overall
        self.assertFalse(titles.is_unreadable_title("/a b"))
        # leading "/" alone is not enough without a slash later in the token
        self.assertFalse(titles.is_unreadable_title("/ 说明 a/b"))

    def test_long_text_boundary(self):
        self.assertFalse(titles.is_unreadable_title("x" * titles._LONG_TEXT))
        self.assertTrue(titles.is_unreadable_title("x" * (titles._LONG_TEXT + 1)))

    def test_short_prose_is_readable(self):
        self.assertFalse(titles.is_unreadable_title("整理 EB-1A 推荐信清单"))


class SanitizeTitleTestCase(unittest.TestCase):
    def test_none_and_blank(self):
        self.assertEqual(titles.sanitize_title(None), "")
        self.assertEqual(titles.sanitize_title("  \t"), "")
        self.assertEqual(titles.sanitize_title(""), "")

    def test_url_branch(self):
        self.assertEqual(titles.sanitize_title(" https://www.example.com/a/slug "),
                         "example.com ▸ slug")

    def test_path_branch_takes_last_component(self):
        self.assertEqual(titles.sanitize_title("/Users/z/Documents/report.pdf"), "report.pdf")
        self.assertEqual(titles.sanitize_title("~/x/dir/"), "dir")

    def test_long_text_branch_clips_at_clause(self):
        long = "第一句话结束。" + "后面还有很多很多内容" * 6
        self.assertEqual(titles.sanitize_title(long), "第一句话结束…")

    def test_long_text_without_boundary_clips_at_window(self):
        long = "a" * 70
        out = titles.sanitize_title(long)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), titles._CLIP_AT + 1)

    def test_short_text_passes_collapsed(self):
        self.assertEqual(titles.sanitize_title("  a   b\nc "), "a b c")

    def test_non_str_input_is_stringified(self):
        self.assertEqual(titles.sanitize_title(456), "456")

    def test_exactly_long_text_is_not_clipped(self):
        t = "b" * titles._LONG_TEXT
        self.assertEqual(titles.sanitize_title(t), t)


if __name__ == "__main__":
    unittest.main()
