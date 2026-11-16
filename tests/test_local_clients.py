"""Safety checks using the installed boto3/botocore, with network I/O mocked."""

import os
import unittest
from unittest.mock import MagicMock, patch

from rehearse.local import connect

try:
    import boto3  # noqa: F401
except ImportError:
    boto3 = None


@unittest.skipUnless(boto3, "install boto3 to test SDK endpoint confinement")
class TestClientConfinement(unittest.TestCase):
    def test_untrusted_ambient_aws_settings_cannot_redirect_clients(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"ok"
        opener = MagicMock()
        opener.open.return_value = response
        ambient = {
            "AWS_PROFILE": "does-not-exist", "AWS_CONFIG_FILE": "/not/here",
            "AWS_ENDPOINT_URL": "https://example.com", "AWS_ENDPOINT_URL_S3": "https://example.com",
            "HTTPS_PROXY": "http://example.com", "AWS_USE_FIPS_ENDPOINT": "true",
        }
        with patch.dict(os.environ, ambient), patch(
            "rehearse.local.read_credentials", return_value=("EMULATOR123", "secret", "/fake/ca")
        ), patch("ssl.create_default_context"), patch(
            "urllib.request.build_opener", return_value=opener
        ), patch("botocore.client.BaseClient._make_api_call", return_value={"Account": "000000000000"}):
            s3, ddb, version = connect()
        self.assertEqual(s3.meta.endpoint_url, "https://127.0.0.1:9297")
        self.assertEqual(ddb.meta.endpoint_url, "https://127.0.0.1:9297")
        self.assertEqual(s3.meta.config.proxies, {})
        self.assertEqual(s3.meta.config.retries["total_max_attempts"], 1)
        self.assertEqual(s3.meta.config.s3["addressing_style"], "path")
        self.assertEqual(s3.meta.config.request_checksum_calculation, "when_required")
        self.assertTrue(version)

    def test_unexpected_account_fails_before_writes(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"ok"
        opener = MagicMock()
        opener.open.return_value = response
        with patch("rehearse.local.read_credentials", return_value=("EMULATOR123", "secret", "/fake/ca")), patch(
            "ssl.create_default_context"
        ), patch("urllib.request.build_opener", return_value=opener), patch(
            "botocore.client.BaseClient._make_api_call", return_value={"Account": "111111111111"}
        ) as api:
            with self.assertRaisesRegex(RuntimeError, "did not return the emulator account"):
                connect()
        self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
