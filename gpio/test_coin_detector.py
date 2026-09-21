from unittest import TestCase
from unittest.mock import patch

from gpio import coin_detector


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

    def test_global_gpio_is_converted_to_chip_local_offset(self):
        chip_ranges = [
            ("/dev/gpiochip0", 0, 224),
            ("/dev/gpiochip1", 224, 32),
        ]
        with patch.object(coin_detector, "_sysfs_gpiochips", return_value=chip_ranges):
            self.assertEqual(
                coin_detector.resolve_gpio(object(), 229),
                ("/dev/gpiochip1", 5),
            )

    def test_explicit_wrong_chip_is_rejected(self):
        chip_ranges = [
            ("/dev/gpiochip0", 0, 288),
            ("/dev/gpiochip1", 288, 32),
        ]
        with patch.object(coin_detector, "_sysfs_gpiochips", return_value=chip_ranges):
            with self.assertRaisesRegex(RuntimeError, "GPIO 229 was not found"):
                coin_detector.resolve_gpio(object(), 229, "/dev/gpiochip1")

    def test_non_gpio_header_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported physical header pin 6"):
            coin_detector.gpio_number_for_header_pin(6)
