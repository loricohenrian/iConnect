#!/usr/bin/env python3
"""
iConnect GPIO coin detector.

Listens for pulses from the ALLAN 1239A coin acceptor and posts them to Django.
Production-only script — runs on Orange Pi / ALLAN H3 hardware.

Shared-slot safe by default: coin events are sent unscoped.
Set DEVICE_SCOPE_ENABLED=true and DEVICE_MAC to opt in to fixed-device scoping.
"""
import os
import sys
import time
import logging

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("coin_detector")

DJANGO_URL = os.getenv("DJANGO_URL", "http://127.0.0.1")
GPIO_PIN = int(os.getenv("GPIO_PIN", "3"))
DEVICE_MAC = os.getenv("DEVICE_MAC", "").upper().strip()
DEVICE_SCOPE_ENABLED = os.getenv("DEVICE_SCOPE_ENABLED", "False").lower() in ("true", "1", "yes")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "iconnect-local-device-key-change-me")
PULSE_TIMEOUT = 0.5
API_ENDPOINT = f"{DJANGO_URL}/api/coin-inserted/"


def device_scope_active():
    return DEVICE_SCOPE_ENABLED and bool(DEVICE_MAC)


def send_coin_event(amount, denomination):
    """Send coin insertion to Django API."""
    payload = {
        "amount": amount,
        "denomination": denomination,
    }
    if device_scope_active():
        payload["mac_address"] = DEVICE_MAC

    try:
        response = requests.post(
            API_ENDPOINT,
            json=payload,
            headers={"X-DEVICE-API-KEY": DEVICE_API_KEY},
            timeout=5,
        )
        data = response.json()
        logger.info("Server response: %s", data.get("message", "OK"))
        if data.get("voucher_code"):
            logger.info("Voucher code: %s", data["voucher_code"])
        return data
    except requests.exceptions.ConnectionError:
        logger.error("Cannot connect to Django at %s", DJANGO_URL)
        return None
    except Exception as exc:
        logger.error("Error sending coin event: %s", exc)
        return None


def run_gpio():
    """Hardware mode for Orange Pi / ALLAN H3."""
    try:
        import OPi.GPIO as GPIO
    except ImportError:
        logger.critical(
            "OPi.GPIO library not installed. "
            "Install with: pip install OPi.GPIO. "
            "This script must run on the Orange Pi hardware."
        )
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("iConnect Coin Detector — PRODUCTION MODE")
    logger.info("=" * 50)
    logger.info("GPIO Pin: %s", GPIO_PIN)
    logger.info("API endpoint: %s", API_ENDPOINT)
    if device_scope_active():
        logger.info("Scoped device MAC: %s", DEVICE_MAC)
    else:
        logger.info("Shared-slot unscoped mode (coin queue attribution)")
    logger.info("Listening for coin pulses...")

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    pulse_count = 0
    last_pulse_time = 0

    try:
        while True:
            if GPIO.input(GPIO_PIN) == GPIO.LOW:
                pulse_count += 1
                last_pulse_time = time.time()
                logger.debug("Pulse #%d", pulse_count)

                while GPIO.input(GPIO_PIN) == GPIO.LOW:
                    time.sleep(0.01)

            if pulse_count > 0 and (time.time() - last_pulse_time) > PULSE_TIMEOUT:
                if pulse_count in (1, 5, 10, 20):
                    amount = pulse_count
                    logger.info("₱%d coin detected (%d pulses)", amount, pulse_count)
                    send_coin_event(amount, amount)
                else:
                    logger.warning("Invalid pulse count: %d. Ignoring.", pulse_count)

                pulse_count = 0

            time.sleep(0.01)
    except KeyboardInterrupt:
        logger.info("Shutting down coin detector...")
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    run_gpio()
