#!/usr/bin/env python3
"""
iConnect GPIO coin detector.

Listens for pulses from the coin acceptor and posts them to Django.
Shared-slot safe by default: coin events are sent unscoped.
Set DEVICE_SCOPE_ENABLED=true and DEVICE_MAC to opt in to fixed-device scoping.
"""
import os
import time

import requests


DJANGO_URL = os.getenv("DJANGO_URL", "http://localhost:8000")
GPIO_PIN = int(os.getenv("GPIO_PIN", "7"))
SIMULATION_MODE = os.getenv("GPIO_SIMULATION", "True").lower() in ("true", "1", "yes")
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
        print(f"  Server response: {data.get('message', 'OK')}")
        if data.get("voucher_code"):
            print(f"  Voucher code: {data['voucher_code']}")
        return data
    except requests.exceptions.ConnectionError:
        print(f"  Cannot connect to Django at {DJANGO_URL}")
        return None
    except Exception as exc:
        print(f"  Error: {exc}")
        return None


def run_simulation():
    """Simulation mode for local development."""
    print("=" * 50)
    print("iConnect Coin Detector - SIMULATION MODE")
    print("=" * 50)
    print(f"API endpoint: {API_ENDPOINT}")
    if device_scope_active():
        print(f"Scoped device MAC: {DEVICE_MAC}")
    elif DEVICE_MAC and not DEVICE_SCOPE_ENABLED:
        print("DEVICE_MAC is set but DEVICE_SCOPE_ENABLED is false; using shared-slot unscoped mode.")
    elif DEVICE_SCOPE_ENABLED and not DEVICE_MAC:
        print("DEVICE_SCOPE_ENABLED is true but DEVICE_MAC is empty; using shared-slot unscoped mode.")
    print()
    print('Enter coin denomination (1, 5, 10, 20) or "q" to quit:')
    print()

    while True:
        try:
            user_input = input("Insert coin: ").strip()
            if user_input.lower() in ("q", "quit", "exit"):
                print("Shutting down...")
                break

            try:
                amount = int(user_input)
            except ValueError:
                print("  Invalid input. Enter 1, 5, 10, or 20.")
                continue

            if amount not in (1, 5, 10, 20):
                print("  Invalid denomination. Accepted: 1, 5, 10, 20.")
                continue

            print(f"  {amount} coin detected ({amount} pulses)")
            send_coin_event(amount, amount)
            print()
        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except EOFError:
            break


def run_gpio():
    """Hardware mode for Orange Pi / ALLAN H3."""
    try:
        import OPi.GPIO as GPIO
    except ImportError:
        print("ERROR: OPi.GPIO not installed. Falling back to simulation mode...")
        run_simulation()
        return

    print("=" * 50)
    print("iConnect Coin Detector - HARDWARE MODE")
    print("=" * 50)
    print(f"GPIO Pin: {GPIO_PIN}")
    print(f"API endpoint: {API_ENDPOINT}")
    if device_scope_active():
        print(f"Scoped device MAC: {DEVICE_MAC}")
    elif DEVICE_MAC and not DEVICE_SCOPE_ENABLED:
        print("DEVICE_MAC is set but DEVICE_SCOPE_ENABLED is false; using shared-slot unscoped mode.")
    elif DEVICE_SCOPE_ENABLED and not DEVICE_MAC:
        print("DEVICE_SCOPE_ENABLED is true but DEVICE_MAC is empty; using shared-slot unscoped mode.")
    print("Listening for coin pulses...")

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    pulse_count = 0
    last_pulse_time = 0

    try:
        while True:
            if GPIO.input(GPIO_PIN) == GPIO.LOW:
                pulse_count += 1
                last_pulse_time = time.time()
                print(f"  Pulse #{pulse_count}")

                while GPIO.input(GPIO_PIN) == GPIO.LOW:
                    time.sleep(0.01)

            if pulse_count > 0 and (time.time() - last_pulse_time) > PULSE_TIMEOUT:
                if pulse_count in (1, 5, 10, 20):
                    amount = pulse_count
                    print(f"  {amount} coin detected ({pulse_count} pulses)")
                    send_coin_event(amount, amount)
                else:
                    print(f"  Invalid pulse count: {pulse_count}. Ignoring.")

                pulse_count = 0
                print()

            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    if SIMULATION_MODE:
        run_simulation()
    else:
        run_gpio()

