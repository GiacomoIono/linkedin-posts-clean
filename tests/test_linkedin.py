from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from pipeline.linkedin import LINKEDIN_CHANGE_LOG_URL, LINKEDIN_VERSION, fetch_latest_linkedin_post


class FixedDatetime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 6, 1, 2, 0, tzinfo=timezone.utc)


class LinkedInTests(unittest.TestCase):
    def test_fetch_latest_linkedin_post_defaults_to_48_hour_window(self) -> None:
        response = Mock(status_code=200, text="")
        response.json.return_value = {"elements": []}

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch("pipeline.linkedin.requests.get", return_value=response) as request_get,
        ):
            self.assertIsNone(fetch_latest_linkedin_post("token"))

        expected_start_time = int(
            (datetime(2026, 6, 1, 2, 0, tzinfo=timezone.utc) - timedelta(hours=48)).timestamp() * 1000
        )
        request_get.assert_called_once()
        args, kwargs = request_get.call_args
        self.assertEqual(args[0], LINKEDIN_CHANGE_LOG_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(kwargs["headers"]["LinkedIn-Version"], LINKEDIN_VERSION)
        self.assertEqual(kwargs["params"]["startTime"], expected_start_time)
        self.assertEqual(kwargs["params"]["count"], 500)


if __name__ == "__main__":
    unittest.main()
