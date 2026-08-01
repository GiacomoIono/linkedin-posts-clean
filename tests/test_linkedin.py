from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call, patch

from pipeline.linkedin import LINKEDIN_CHANGE_LOG_URL, LINKEDIN_VERSION, fetch_latest_linkedin_post


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 1, 2, 0, tzinfo=timezone.utc)


def api_response(elements=None, status_code: int = 200, text: str = "") -> Mock:
    response = Mock(status_code=status_code, text=text)
    response.json.return_value = {"elements": elements or []}
    return response


def change(timestamp: int, processed_at: int | None = None) -> dict:
    return {
        "resourceName": "comments",
        "method": "CREATE",
        "capturedAt": timestamp,
        "processedAt": timestamp if processed_at is None else processed_at,
    }


def ugc_post(
    timestamp: int,
    resource_id: str = "urn:li:ugcPost:123",
    processed_at: int | None = None,
) -> dict:
    return {
        "resourceName": "ugcPosts",
        "method": "CREATE",
        "capturedAt": timestamp,
        "processedAt": timestamp if processed_at is None else processed_at,
        "resourceId": resource_id,
        "activity": {
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": "A LinkedIn post"},
                }
            }
        },
    }


class LinkedInTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime(2026, 6, 1, 2, 0, tzinfo=timezone.utc)
        self.cutoff = int((now - timedelta(hours=48)).timestamp() * 1000)

    def test_fetch_latest_linkedin_post_defaults_to_48_hour_window(self) -> None:
        response = api_response()

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch("pipeline.linkedin.requests.get", return_value=response) as request_get,
        ):
            self.assertIsNone(fetch_latest_linkedin_post("token"))

        request_get.assert_called_once()
        args, kwargs = request_get.call_args
        self.assertEqual(args[0], LINKEDIN_CHANGE_LOG_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(kwargs["headers"]["LinkedIn-Version"], LINKEDIN_VERSION)
        self.assertEqual(kwargs["params"]["startTime"], self.cutoff)
        self.assertEqual(kwargs["params"]["count"], 50)
        self.assertEqual(kwargs["params"]["start"], 0)

    def test_paginates_until_every_record_in_window_has_been_checked(self) -> None:
        page_one = [ugc_post(self.cutoff + 2_000, "urn:li:ugcPost:older")] + [
            change(self.cutoff + 5_000) for _ in range(49)
        ]
        target_timestamp = self.cutoff + 4_000
        page_two = [ugc_post(target_timestamp, "urn:li:ugcPost:latest")] + [
            change(self.cutoff + 3_000) for _ in range(49)
        ]

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch(
                "pipeline.linkedin.requests.get",
                side_effect=[api_response(page_one), api_response(page_two), api_response()],
            ) as request_get,
        ):
            post = fetch_latest_linkedin_post("token")

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:latest")
        self.assertEqual([request.kwargs["params"]["start"] for request in request_get.call_args_list], [0, 50, 100])
        self.assertTrue(all(request.kwargs["params"]["count"] == 50 for request in request_get.call_args_list))

    def test_stops_requesting_pages_after_older_records(self) -> None:
        target_timestamp = self.cutoff + 5_000
        page_one = [ugc_post(target_timestamp)] + [change(self.cutoff + 4_000) for _ in range(49)]
        page_two = [change(self.cutoff - 1, processed_at=self.cutoff - 1) for _ in range(50)]

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch(
                "pipeline.linkedin.requests.get",
                side_effect=[api_response(page_one), api_response(page_two)],
            ) as request_get,
        ):
            post = fetch_latest_linkedin_post("token")

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:123")
        self.assertEqual([request.kwargs["params"]["start"] for request in request_get.call_args_list], [0, 50])

    def test_keeps_a_delayed_record_processed_inside_the_window(self) -> None:
        delayed_post = ugc_post(
            self.cutoff - 10_000,
            resource_id="urn:li:ugcPost:delayed",
            processed_at=self.cutoff + 1_000,
        )

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch("pipeline.linkedin.requests.get", return_value=api_response([delayed_post])),
        ):
            post = fetch_latest_linkedin_post("token")

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:delayed")

    def test_includes_a_record_exactly_at_the_lookback_boundary(self) -> None:
        boundary_post = ugc_post(self.cutoff, processed_at=self.cutoff)

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch("pipeline.linkedin.requests.get", return_value=api_response([boundary_post])),
        ):
            post = fetch_latest_linkedin_post("token")

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:123")

    def test_retries_a_temporary_500_error(self) -> None:
        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch(
                "pipeline.linkedin.requests.get",
                side_effect=[api_response(status_code=500, text="temporary"), api_response()],
            ) as request_get,
            patch("pipeline.linkedin.time.sleep") as sleep,
        ):
            self.assertIsNone(fetch_latest_linkedin_post("token"))

        self.assertEqual(request_get.call_count, 2)
        self.assertEqual([request.kwargs["params"]["start"] for request in request_get.call_args_list], [0, 0])
        sleep.assert_called_once_with(1)

    def test_retries_the_same_offset_when_a_later_page_returns_500(self) -> None:
        page_one = [change(self.cutoff + 2_000) for _ in range(50)]
        target = ugc_post(self.cutoff + 1_000)

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch(
                "pipeline.linkedin.requests.get",
                side_effect=[
                    api_response(page_one),
                    api_response(status_code=500, text="temporary"),
                    api_response([target]),
                ],
            ) as request_get,
            patch("pipeline.linkedin.time.sleep") as sleep,
        ):
            post = fetch_latest_linkedin_post("token")

        self.assertEqual(post["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:123")
        self.assertEqual([request.kwargs["params"]["start"] for request in request_get.call_args_list], [0, 50, 50])
        sleep.assert_called_once_with(1)

    def test_raises_after_three_500_responses(self) -> None:
        responses = [api_response(status_code=500, text="temporary") for _ in range(3)]

        with (
            patch("pipeline.linkedin.datetime", FixedDatetime),
            patch("pipeline.linkedin.requests.get", side_effect=responses) as request_get,
            patch("pipeline.linkedin.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "LinkedIn API failed: 500 temporary"),
        ):
            fetch_latest_linkedin_post("token")

        self.assertEqual(request_get.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])


if __name__ == "__main__":
    unittest.main()
