"""
iConnect GPIO Hardware Feedback (Buzzer and LEDs)

Handles physical feedback at the PisoWiFi machine using GPIO pins.
Gracefully degrades when not running on Orange Pi hardware.
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

# Pin configuration (using BOARD numbering)
# Customize these based on actual PisoWiFi board layout
BUZZER_PIN = int(os.getenv("GPIO_BUZZER_PIN", "11"))
LED_GREEN_PIN = int(os.getenv("GPIO_LED_GREEN_PIN", "13"))
LED_RED_PIN = int(os.getenv("GPIO_LED_RED_PIN", "15"))

_GPIO_READY = False
_GPIO_AVAILABLE = False


def _load_gpio():
    """Attempt to load the GPIO library."""
    global _GPIO_AVAILABLE
    try:
        import OPi.GPIO  # noqa: F401
        _GPIO_AVAILABLE = True
    except ImportError:
        _GPIO_AVAILABLE = False
    return _GPIO_AVAILABLE


# Attempt on module load
_load_gpio()


def setup_gpio():
    """Initialize GPIO pins for feedback."""
    global _GPIO_READY
    if not _GPIO_AVAILABLE:
        logger.warning("OPi.GPIO not available — hardware feedback disabled")
        return False

    try:
        import OPi.GPIO as GPIO
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(LED_GREEN_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(LED_RED_PIN, GPIO.OUT, initial=GPIO.LOW)
        _GPIO_READY = True
        logger.info("GPIO feedback pins initialized (Buzzer=%d, Green=%d, Red=%d)",
                     BUZZER_PIN, LED_GREEN_PIN, LED_RED_PIN)
        return True
    except Exception as e:
        logger.error("Failed to initialize GPIO feedback: %s", e)
        return False


def _trigger(pin, duration=0.1):
    """Internal helper to pulse a pin."""
    if not _GPIO_AVAILABLE:
        return

    if not _GPIO_READY and not setup_gpio():
        return

    try:
        import OPi.GPIO as GPIO
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(pin, GPIO.LOW)
    except Exception as e:
        logger.error("GPIO trigger error on pin %d: %s", pin, e)


def pulse_success():
    """Triggered on successful payment or session start."""
    if not _GPIO_AVAILABLE:
        return

    if not _GPIO_READY and not setup_gpio():
        return

    try:
        import OPi.GPIO as GPIO
        GPIO.output(LED_GREEN_PIN, GPIO.HIGH)
        _trigger(BUZZER_PIN, 0.15)
        GPIO.output(LED_GREEN_PIN, GPIO.LOW)
    except Exception as e:
        logger.error("GPIO pulse_success error: %s", e)


def pulse_error():
    """Triggered on payment error or authentication failure."""
    if not _GPIO_AVAILABLE:
        return

    if not _GPIO_READY and not setup_gpio():
        return

    try:
        import OPi.GPIO as GPIO
        GPIO.output(LED_RED_PIN, GPIO.HIGH)
        _trigger(BUZZER_PIN, 0.5)
        GPIO.output(LED_RED_PIN, GPIO.LOW)
    except Exception as e:
        logger.error("GPIO pulse_error error: %s", e)


def pulse_expiration():
    """Triggered when time runs out."""
    if not _GPIO_AVAILABLE:
        return

    _trigger(BUZZER_PIN, 0.1)
    time.sleep(0.1)
    _trigger(BUZZER_PIN, 0.1)


def cleanup():
    """Clean up GPIO pins."""
    if _GPIO_AVAILABLE and _GPIO_READY:
        try:
            import OPi.GPIO as GPIO
            GPIO.cleanup([BUZZER_PIN, LED_GREEN_PIN, LED_RED_PIN])
        except Exception as e:
            logger.error("GPIO cleanup error: %s", e)
