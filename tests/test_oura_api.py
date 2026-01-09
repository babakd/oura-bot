"""Tests for Oura API sleep data fetching edge cases."""

import pytest
from unittest.mock import patch

from oura_agent.api.oura import get_oura_sleep_data, get_oura_daily_data


class TestSleepDataMissingEdgeCase:
    """Test cases for when Oura hasn't synced the latest sleep data."""

    def test_get_oura_sleep_data_no_matching_session(self):
        """
        Bug reproduction: When querying for Jan 4 sleep, but only Jan 2->3 sleep exists.
        Should return empty sleep list, NOT the stale session.
        """
        # Mock API returning only an older session (Jan 2->3)
        mock_response = {
            "data": [{
                "id": "sleep-old",
                "day": "2026-01-02",
                "bedtime_start": "2026-01-02T23:30:00-05:00",
                "bedtime_end": "2026-01-03T07:15:00-05:00",  # Ended on Jan 3, NOT Jan 4
                "total_sleep_duration": 25200,
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            # Query for Jan 4 wake date
            result = get_oura_sleep_data("fake_token", "2026-01-04")

            # Should NOT return the Jan 2->3 session
            assert result["sleep"] == [], \
                "Should return empty list when no matching sleep session for wake_date"

    def test_get_oura_sleep_data_correct_session_found(self):
        """When the correct session exists, it should be returned."""
        mock_response = {
            "data": [{
                "id": "sleep-correct",
                "type": "long_sleep",  # Required for filtering
                "day": "2026-01-03",
                "bedtime_start": "2026-01-03T23:30:00-05:00",
                "bedtime_end": "2026-01-04T07:15:00-05:00",  # Ended on Jan 4 - correct!
                "total_sleep_duration": 25200,
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-04")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "sleep-correct"

    def test_get_oura_sleep_data_multiple_sessions_picks_correct(self):
        """When multiple sessions exist, should pick the one matching wake_date."""
        mock_response = {
            "data": [
                {
                    "id": "sleep-old",
                    "type": "long_sleep",
                    "day": "2026-01-02",
                    "bedtime_start": "2026-01-02T23:30:00-05:00",
                    "bedtime_end": "2026-01-03T07:15:00-05:00",  # Wrong date
                    "total_sleep_duration": 25200,
                },
                {
                    "id": "sleep-correct",
                    "type": "long_sleep",
                    "day": "2026-01-03",
                    "bedtime_start": "2026-01-03T23:30:00-05:00",
                    "bedtime_end": "2026-01-04T07:15:00-05:00",  # Correct date
                    "total_sleep_duration": 25200,
                },
            ]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-04")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "sleep-correct"

    def test_get_oura_sleep_data_empty_response(self):
        """When API returns no sessions, should return empty list."""
        mock_response = {"data": []}

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-04")

            assert result["sleep"] == []

    def test_get_oura_sleep_data_nap_not_confused_with_main_sleep(self):
        """
        Edge case: A nap on Jan 3 (ends Jan 3) should not be used for Jan 4 morning brief.
        Only sleep that ends on Jan 4 should be used.
        """
        mock_response = {
            "data": [
                {
                    "id": "nap",
                    "day": "2026-01-03",
                    "bedtime_start": "2026-01-03T14:00:00-05:00",
                    "bedtime_end": "2026-01-03T15:00:00-05:00",  # Nap ended on Jan 3
                    "total_sleep_duration": 3600,
                },
            ]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-04")

            assert result["sleep"] == [], \
                "Nap from previous day should not be used for morning brief"


class TestGetOuraDailyDataSleepMatching:
    """Same tests for get_oura_daily_data() which has the same bug pattern."""

    def test_get_oura_daily_data_no_matching_sleep(self):
        """Should return empty sleep when no matching session exists."""
        def mock_fetch(token, endpoint, start, end):
            if endpoint == "sleep":
                return {"data": [{
                    "id": "sleep-old",
                    "bedtime_end": "2026-01-03T07:15:00-05:00",  # Wrong date
                }]}
            return {"data": []}

        with patch('oura_agent.api.oura.fetch_oura_data', side_effect=mock_fetch):
            result = get_oura_daily_data("fake_token", "2026-01-04")

            assert result["sleep"] == [], \
                "Should return empty list when no matching sleep session"

    def test_get_oura_daily_data_correct_sleep_found(self):
        """Should return sleep when matching session exists."""
        def mock_fetch(token, endpoint, start, end):
            if endpoint == "sleep":
                return {"data": [{
                    "id": "sleep-correct",
                    "type": "long_sleep",  # Required for filtering
                    "bedtime_end": "2026-01-04T07:15:00-05:00",  # Correct date
                }]}
            return {"data": []}

        with patch('oura_agent.api.oura.fetch_oura_data', side_effect=mock_fetch):
            result = get_oura_daily_data("fake_token", "2026-01-04")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "sleep-correct"

    def test_get_oura_daily_data_multiple_sessions_picks_correct(self):
        """When multiple sessions exist, should pick the one matching wake_date."""
        def mock_fetch(token, endpoint, start, end):
            if endpoint == "sleep":
                return {"data": [
                    {
                        "id": "sleep-old",
                        "type": "long_sleep",
                        "bedtime_end": "2026-01-03T07:15:00-05:00",
                    },
                    {
                        "id": "sleep-correct",
                        "type": "long_sleep",
                        "bedtime_end": "2026-01-04T07:15:00-05:00",
                    },
                ]}
            return {"data": []}

        with patch('oura_agent.api.oura.fetch_oura_data', side_effect=mock_fetch):
            result = get_oura_daily_data("fake_token", "2026-01-04")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "sleep-correct"


class TestSleepTypeFiltering:
    """Test cases for filtering sleep sessions by type (long_sleep vs rest/sleep/late_nap)."""

    def test_get_oura_sleep_data_filters_out_rest_type(self):
        """
        When ring is removed during sleep, Oura returns type: "rest" sessions.
        These should be filtered out, not used as main sleep.
        """
        mock_response = {
            "data": [{
                "id": "rest-session",
                "type": "rest",  # NOT long_sleep - should be filtered
                "day": "2026-01-08",
                "bedtime_start": "2026-01-08T02:39:00-05:00",
                "bedtime_end": "2026-01-08T03:12:00-05:00",
                "total_sleep_duration": 1020,  # 17 minutes
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-08")

            assert result["sleep"] == [], \
                "type: 'rest' sessions should be filtered out"

    def test_get_oura_sleep_data_filters_out_short_sleep_type(self):
        """
        Short sleep fragments (type: "sleep") should be filtered out.
        Only type: "long_sleep" (3+ hours) should be used.
        """
        mock_response = {
            "data": [{
                "id": "short-sleep",
                "type": "sleep",  # NOT long_sleep - should be filtered
                "day": "2026-01-08",
                "bedtime_start": "2026-01-08T00:45:00-05:00",
                "bedtime_end": "2026-01-08T01:10:00-05:00",
                "total_sleep_duration": 780,  # 13 minutes
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-08")

            assert result["sleep"] == [], \
                "type: 'sleep' (short fragment) sessions should be filtered out"

    def test_get_oura_sleep_data_filters_out_late_nap_type(self):
        """Late nap sessions (type: "late_nap") should be filtered out."""
        mock_response = {
            "data": [{
                "id": "late-nap",
                "type": "late_nap",  # NOT long_sleep - should be filtered
                "day": "2026-01-07",
                "bedtime_start": "2026-01-07T20:00:00-05:00",
                "bedtime_end": "2026-01-08T00:30:00-05:00",
                "total_sleep_duration": 14400,  # 4 hours but wrong type
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-08")

            assert result["sleep"] == [], \
                "type: 'late_nap' sessions should be filtered out"

    def test_get_oura_sleep_data_accepts_long_sleep_type(self):
        """Only type: "long_sleep" sessions should be accepted."""
        mock_response = {
            "data": [{
                "id": "main-sleep",
                "type": "long_sleep",  # Correct type - should be used
                "day": "2026-01-07",
                "bedtime_start": "2026-01-07T23:30:00-05:00",
                "bedtime_end": "2026-01-08T07:15:00-05:00",
                "total_sleep_duration": 25200,  # 7 hours
            }]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-08")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "main-sleep"

    def test_get_oura_sleep_data_picks_long_sleep_over_fragments(self):
        """
        When multiple sessions exist (fragments + long_sleep),
        should pick the long_sleep one.
        """
        mock_response = {
            "data": [
                {
                    "id": "rest-fragment",
                    "type": "rest",
                    "bedtime_end": "2026-01-08T01:10:00-05:00",
                },
                {
                    "id": "sleep-fragment",
                    "type": "sleep",
                    "bedtime_end": "2026-01-08T03:12:00-05:00",
                },
                {
                    "id": "main-sleep",
                    "type": "long_sleep",
                    "bedtime_end": "2026-01-08T07:15:00-05:00",
                },
            ]
        }

        with patch('oura_agent.api.oura.fetch_oura_data') as mock_fetch:
            mock_fetch.return_value = mock_response

            result = get_oura_sleep_data("fake_token", "2026-01-08")

            assert len(result["sleep"]) == 1
            assert result["sleep"][0]["id"] == "main-sleep"

    def test_get_oura_daily_data_filters_by_type(self):
        """Same type filtering should apply to get_oura_daily_data()."""
        def mock_fetch(token, endpoint, start, end):
            if endpoint == "sleep":
                return {"data": [{
                    "id": "rest-session",
                    "type": "rest",  # Should be filtered
                    "bedtime_end": "2026-01-08T03:12:00-05:00",
                }]}
            return {"data": []}

        with patch('oura_agent.api.oura.fetch_oura_data', side_effect=mock_fetch):
            result = get_oura_daily_data("fake_token", "2026-01-08")

            assert result["sleep"] == [], \
                "get_oura_daily_data should also filter by type"