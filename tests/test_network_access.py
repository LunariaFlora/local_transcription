from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.app_server import lan_ip_address, network_state


class NetworkAccessTests(unittest.TestCase):
    def test_configured_lan_address_is_used_for_phone_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LOCAL_TRANSCRIPTION_LAN_IP": "192.168.50.12",
                "LOCAL_TRANSCRIPTION_PORT": "9876",
            },
        ):
            self.assertEqual(lan_ip_address(), "192.168.50.12")
            self.assertEqual(
                network_state(),
                {
                    "local_url": "http://127.0.0.1:9876",
                    "phone_url": "http://192.168.50.12:9876",
                },
            )


if __name__ == "__main__":
    unittest.main()
