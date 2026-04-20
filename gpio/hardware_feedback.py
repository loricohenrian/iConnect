"""
iConnect GPIO Hardware Feedback (Buzzer and LEDs)

Handles physical feedback at the PisoWiFi machine using GPIO pins.
Supports simulation mode for local development.
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

SIMULATION_MODE = os.getenv("GPIO_SIMULATION", "True").lower() in ("true", "1", "yes")

_GPIO_READY = False

def setup_gpio():
    """Initialize GPIO pins for feedback."""
    global _GPIO_READY
    if SIMULATION_MODE:
        logger.info("[SIMULATION] GPIO Feedback initialized")
        return True

    try:
        import OPi.GPIO as GPIO
        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(LED_GREEN_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(LED_RED_PIN, GPIO.OUT, initial=GPIO.LOW)
        _GPIO_READY = True
        return True
    except (ImportError, Exception) as e:
        logger.error(f"Failed to initialize GPIO Feedback: {e}")
        return False

def _trigger(pin, duration=0.1):
    """Internal helper to pulse a pin."""
    if SIMULATION_MODE:
        logger.info(f"[SIMULATION] Pulsing Pin {pin} for {duration}s")
        return

    if not _GPIO_READY and not setup_gpio():
        return

    import OPi.GPIO as GPIO
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(pin, GPIO.LOW)

def pulse_success():
    """Triggered on successful payment or session start."""
    # 1 short beep + Green LED flash
    if SIMULATION_MODE:
        logger.info("Feedback: SUCCESS (1 beep, Green LED)")
        _trigger(BUZZER_PIN, 0.15)
        _trigger(LED_GREEN_PIN, 0.3)
    else:
        # Real GPIO logic
        import OPi.GPIO as GPIO
        GPIO.output(LED_GREEN_PIN, GPIO.HIGH)
        _trigger(BUZZER_PIN, 0.15)
        GPIO.output(LED_GREEN_PIN, GPIO.LOW)

def pulse_error():
    """Triggered on payment error or authentication failure."""
    # 1 long beep + Red LED flash
    if SIMULATION_MODE:
        logger.info("Feedback: ERROR (1 long beep, Red LED)")
        _trigger(BUZZER_PIN, 0.5)
        _trigger(LED_RED_PIN, 0.5)
    else:
        import OPi.GPIO as GPIO
        GPIO.output(LED_RED_PIN, GPIO.HIGH)
        _trigger(BUZZER_PIN, 0.5)
        GPIO.output(LED_RED_PIN, GPIO.LOW)

def pulse_expiration():
    """Triggered when time runs out."""
    # 2 short beeps
    if SIMULATION_MODE:
        logger.info("Feedback: EXPIRATION (2 short beeps)")
        _trigger(BUZZER_PIN, 0.1)
        time.sleep(0.1)
        _trigger(BUZZER_PIN, 0.1)
    else:
        _trigger(BUZZER_PIN, 0.1)
        time.sleep(0.1)
        _trigger(BUZZER_PIN, 0.1)

# Cleanup helper
def cleanup():
    if not SIMULATION_MODE and _GPIO_READY:
        import OPi.GPIO as GPIO
        GPIO.cleanup([BUZZER_PIN, LED_GREEN_PIN, LED_RED_PIN])
