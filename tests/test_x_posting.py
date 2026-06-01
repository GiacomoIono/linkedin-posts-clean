from __future__ import annotations

import unittest

from pipeline.x_posting import selected_tweet_images, tweet_image_urls


class XPostingTests(unittest.TestCase):
    def test_tweet_image_urls_keeps_first_four_valid_urls(self) -> None:
        post = {
            "images": [
                {"url": "https://example.com/1.jpg"},
                {"url": "https://example.com/2.jpg"},
                {"url": ""},
                "not-an-image",
                {"url": "https://example.com/3.jpg"},
                {"url": "https://example.com/4.jpg"},
                {"url": "https://example.com/5.jpg"},
            ]
        }

        self.assertEqual(
            tweet_image_urls(post),
            [
                "https://example.com/1.jpg",
                "https://example.com/2.jpg",
                "https://example.com/3.jpg",
                "https://example.com/4.jpg",
            ],
        )

    def test_selected_tweet_images_keeps_only_images_from_the_post(self) -> None:
        data = {
            "images": [
                {"url": "https://example.com/1.jpg", "alt": '"Clean alt text"'},
                {"url": "https://example.com/not-from-post.jpg", "alt": "Ignore this"},
                "not-an-image",
            ]
        }

        self.assertEqual(
            selected_tweet_images(data, ["https://example.com/1.jpg"]),
            [{"url": "https://example.com/1.jpg", "alt": "Clean alt text"}],
        )


if __name__ == "__main__":
    unittest.main()
