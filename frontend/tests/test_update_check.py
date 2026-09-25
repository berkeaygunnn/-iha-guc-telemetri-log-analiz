"""update_check.py için birim testleri. Tk'ye gerek yok, gerçek ağ isteği
YAPILMAZ (urllib.request.urlopen hep mock'lanır)."""

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

import update_check


class ParseVersionTests(unittest.TestCase):
    def test_plain_version(self):
        self.assertEqual(update_check.parse_version("0.1.0"), (0, 1, 0))

    def test_v_prefixed_version(self):
        self.assertEqual(update_check.parse_version("v0.1.0"), (0, 1, 0))

    def test_two_part_version(self):
        self.assertEqual(update_check.parse_version("v2.5"), (2, 5))

    def test_malformed_returns_none(self):
        self.assertIsNone(update_check.parse_version("not-a-version"))

    def test_empty_returns_none(self):
        self.assertIsNone(update_check.parse_version(""))
        self.assertIsNone(update_check.parse_version(None))


class IsNewerVersionTests(unittest.TestCase):
    def test_newer_patch(self):
        self.assertTrue(update_check.is_newer_version("v0.1.1", "0.1.0"))

    def test_newer_minor(self):
        self.assertTrue(update_check.is_newer_version("v0.2.0", "0.1.9"))

    def test_equal_is_not_newer(self):
        self.assertFalse(update_check.is_newer_version("v0.1.0", "0.1.0"))

    def test_older_is_not_newer(self):
        self.assertFalse(update_check.is_newer_version("v0.1.0", "0.2.0"))

    def test_malformed_remote_is_not_newer(self):
        self.assertFalse(update_check.is_newer_version("bozuk", "0.1.0"))


def _fake_response(payload: dict, status: int = 200):
    body = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class FetchLatestReleaseTests(unittest.TestCase):
    def _payload(self, tag="v0.1.1", assets=None):
        if assets is None:
            assets = [
                {"name": "iha-guc-telemetri-analiz-v0.1.1-windows.zip",
                 "browser_download_url": "https://example.com/zip"},
                {"name": "IHA_Log_Analiz_Kurulum_v0.1.1.exe",
                 "browser_download_url": "https://example.com/installer.exe"},
            ]
        return {"tag_name": tag, "assets": assets}

    @patch("urllib.request.urlopen")
    def test_successful_response_picks_exe_not_zip(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(self._payload())
        result = update_check.fetch_latest_release()
        self.assertEqual(result, {
            "version": "v0.1.1",
            "installer_name": "IHA_Log_Analiz_Kurulum_v0.1.1.exe",
            "installer_url": "https://example.com/installer.exe",
        })

    @patch("urllib.request.urlopen")
    def test_network_error_returns_none_without_raising(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("no internet")
        result = update_check.fetch_latest_release()
        self.assertIsNone(result)

    @patch("urllib.request.urlopen")
    def test_non_200_status_returns_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(self._payload(), status=404)
        self.assertIsNone(update_check.fetch_latest_release())

    @patch("urllib.request.urlopen")
    def test_malformed_json_returns_none(self, mock_urlopen):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"{not json"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response
        self.assertIsNone(update_check.fetch_latest_release())

    @patch("urllib.request.urlopen")
    def test_no_matching_exe_asset_returns_none(self, mock_urlopen):
        payload = self._payload(assets=[
            {"name": "source.zip", "browser_download_url": "https://example.com/zip"},
        ])
        mock_urlopen.return_value = _fake_response(payload)
        self.assertIsNone(update_check.fetch_latest_release())

    @patch("urllib.request.urlopen")
    def test_malformed_tag_name_returns_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(self._payload(tag="not-a-version"))
        self.assertIsNone(update_check.fetch_latest_release())


if __name__ == "__main__":
    unittest.main()
