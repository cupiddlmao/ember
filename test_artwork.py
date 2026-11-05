import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import artwork


class ArtworkTests(unittest.TestCase):
    def test_https_host_allowlist(self):
        allowed = (
            "https://i.ytimg.com/vi/dm4tkSNKfFI/hqdefault.jpg",
            "https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg",
            "https://yt3.googleusercontent.com/example=w120-h120",
            "https://lh3.googleusercontent.com/example=w544-h544",
        )
        for url in allowed:
            self.assertIsNotNone(artwork.artwork_url(url), url)
        rejected = (
            "http://i.ytimg.com/vi/dm4tkSNKfFI/hqdefault.jpg",
            "https://i.ytimg.com.evil.test/art.jpg",
            "https://user@i.ytimg.com/art.jpg",
            "https://i.ytimg.com:8443/art.jpg",
            "https://example.test/art.jpg",
            "file:///etc/passwd",
            "",
            None,
        )
        for url in rejected:
            self.assertIsNone(artwork.artwork_url(url), repr(url))

    def test_i_ytimg_uses_img_youtube_fallback(self):
        self.assertEqual(
            artwork.alternate_artwork_url("https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg"),
            "https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg",
        )
        self.assertIsNone(artwork.alternate_artwork_url("https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg"))

    def test_failed_primary_download_retries_img_youtube(self):
        with patch("artwork._download", side_effect=[urllib.error.URLError("blocked"), b"image"]) as fetch:
            result = artwork.download_artwork("https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg")
        self.assertEqual(result, b"image")
        self.assertEqual(fetch.call_args_list[1].args[0], "https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg")

    def test_redirect_targets_are_revalidated(self):
        request = urllib.request.Request("https://i.ytimg.com/vi/dm4tkSNKfFI/hqdefault.jpg")
        handler = artwork.SafeArtworkRedirect()
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.test/art.jpg")
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://i.ytimg.com/art.jpg")

    def test_invalid_urls_do_not_reach_network(self):
        with self.assertRaises(ValueError):
            artwork.download_artwork("https://evil.test/art.jpg")
        with self.assertRaises(ValueError):
            artwork.download_artwork("http://i.ytimg.com/art.jpg")


if __name__ == "__main__":
    unittest.main()
