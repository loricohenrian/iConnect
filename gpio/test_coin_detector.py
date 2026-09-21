from unittest import TestCase
from unittest.mock import patch

from gpio import coin_detector


class FakeChip:
    def __init__(self, line_count):
        self.line_count = line_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def get_info(self):
        return type("ChipInfo", (), {"num_lines": self.line_count})()


class FakeGpiod:
    def __init__(self, line_counts):
        self.line_counts = line_counts

    def Chip(self, device):
        return FakeChip(self.line_counts[device])


class CoinDetectorConfigurationTests(TestCase):
    def test_default_header_pin_mapping_matches_zero_3_pinout(self):
        self.assertEqual(coin_detector.gpio_number_for_header_pin(3), 229)
        self.assertEqual(coin_detector.gpio_number_for_header_pin(5), 228)
        self.assertEqual(coin_detector.gpio_number_for_header_pin(8), 226)

    def test_pisowifi_defaults_use_board_gpio_labels(self):
        self.assertEqual(coin_detector.GPIO_PIN, 3)
        self.assertEqual(coin_detector.COIN_RELAY_PIN, 5)

    def test_global_gpio_number_remains_supported(self):
        self.assertEqual(coin_detector.gpio_number_for_header_pin(229), 229)

    def test_auto_selects_zero_3_main_288_line_controller(self):
        line_counts = {
            "/dev/gpiochip0": 32,
            "/dev/gpiochip1": 288,
        }
        fake_gpiod = FakeGpiod(line_counts)
        with (
            patch.object(coin_detector.glob, "glob", return_value=list(line_counts)),
            patch.object(coin_detector.os.path, "exists", return_value=True),
        ):
            self.assertEqual(
                coin_detector.resolve_gpio(fake_gpiod, 229),
                ("/dev/gpiochip1", 229),
            )

    def test_explicit_wrong_chip_is_rejected(self):
        fake_gpiod = FakeGpiod({"/dev/gpiochip0": 32})
        with patch.object(coin_detector.os.path, "exists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "GPIO 229 was not found"):
                coin_detector.resolve_gpio(fake_gpiod, 229, "/dev/gpiochip0")

    def test_non_gpio_header_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported physical header pin 6"):
            coin_detector.gpio_number_for_header_pin(6)
