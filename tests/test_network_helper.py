from __future__ import annotations

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chaski-network"
loader = importlib.machinery.SourceFileLoader("chaski_network_helper", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
network = importlib.util.module_from_spec(spec)
loader.exec_module(network)


class NetworkValidationTests(unittest.TestCase):
    def test_static_ipv4_is_normalized(self) -> None:
        with mock.patch.object(network.Path, "exists", return_value=True):
            settings = network.validate(
                {
                    "interface": "eth0",
                    "mode": "static",
                    "address": "192.168.50.20/24",
                    "gateway": "192.168.50.1",
                    "dns": "1.1.1.1, 8.8.8.8",
                    "ssid": "",
                    "password": "",
                }
            )
        self.assertEqual(settings["reconnect_address"], "192.168.50.20")
        self.assertEqual(settings["dns"], ["1.1.1.1", "8.8.8.8"])

    def test_interface_command_injection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid"):
            network.validate({"interface": "eth0;reboot", "mode": "dhcp"})

    def test_short_wifi_password_is_rejected(self) -> None:
        with mock.patch.object(network.Path, "exists", return_value=True), self.assertRaisesRegex(ValueError, "at least 8"):
            network.validate({"interface": "wlan0", "mode": "dhcp", "ssid": "Museum", "password": "short"})

    def test_gateway_outside_static_subnet_is_rejected(self) -> None:
        with mock.patch.object(network.Path, "exists", return_value=True), self.assertRaisesRegex(ValueError, "subnet"):
            network.validate(
                {
                    "interface": "eth0",
                    "mode": "static",
                    "address": "192.168.50.20/24",
                    "gateway": "192.168.60.1",
                }
            )


if __name__ == "__main__":
    unittest.main()
