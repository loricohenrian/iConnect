"""
iConnect Dashboard Input Security Validators and Sanitizers
Provides strict validation for admin inputs, password complexity, and data sanitization.
"""
import re
import html
from django.core.exceptions import ValidationError


def validate_password_strength(password, username=None):
    """
    Validates that a password meets complexity rules:
    - At least 8 characters
    - At least one uppercase letter (A-Z)
    - At least one lowercase letter (a-z)
    - At least one digit (0-9)
    - At least one special symbol (!@#$%^&*()_+-=[]{}|;:,.<>?/~`)
    - No whitespace
    - Cannot match username
    Returns (is_valid, error_message).
    """
    if not password:
        return False, "Password cannot be empty."

    if len(password) < 8:
        return False, "Password must be at least 8 characters long."

    if len(password) > 128:
        return False, "Password cannot exceed 128 characters."

    if re.search(r'\s', password):
        return False, "Password cannot contain spaces or whitespace characters."

    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter (A-Z)."

    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter (a-z)."

    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number (0-9)."

    if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/~`]', password):
        return False, "Password must contain at least one special symbol (e.g. !@#$%^&*)."

    if username and password.lower() == username.lower():
        return False, "Password cannot be the same as the username."

    return True, ""


def validate_username(username):
    """
    Validates an admin username:
    - 3 to 30 characters
    - Alphanumeric, underscores, and hyphens only
    Returns (is_valid, error_message).
    """
    if not username:
        return False, "Username cannot be empty."

    username = username.strip()

    if len(username) < 3:
        return False, "Username must be at least 3 characters long."

    if len(username) > 30:
        return False, "Username cannot exceed 30 characters."

    if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
        return False, "Username can only contain letters, numbers, hyphens, and underscores."

    return True, ""


def sanitize_text(text, max_length=None, allow_multiline=False):
    """
    Sanitizes string inputs:
    - Strips whitespace and null bytes
    - Escapes dangerous HTML entities (<, >, &, \", \') to prevent stored XSS
    - Enforces max_length truncation if requested
    """
    if not text:
        return ""

    # Strip null bytes and control chars
    clean = str(text).replace('\x00', '').strip()

    if not allow_multiline:
        clean = " ".join(clean.splitlines())

    # Escape HTML tags
    clean = html.escape(clean)

    if max_length and len(clean) > max_length:
        clean = clean[:max_length]

    return clean


def parse_bounded_int(val, min_val, max_val, field_name="Value", default=None):
    """
    Safely parses an integer and ensures it falls within [min_val, max_val].
    Raises ValueError with a helpful message on failure.
    """
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError(f"{field_name} is required.")

    try:
        int_val = int(val)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid whole number.")

    if int_val < min_val:
        raise ValueError(f"{field_name} must be at least {min_val}.")
    if int_val > max_val:
        raise ValueError(f"{field_name} cannot exceed {max_val:,}.")

    return int_val


def parse_bounded_float(val, min_val, max_val, field_name="Value", default=None):
    """
    Safely parses a float/decimal and ensures it falls within [min_val, max_val].
    Raises ValueError with a helpful message on failure.
    """
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError(f"{field_name} is required.")

    try:
        float_val = float(val)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number.")

    if float_val < min_val:
        raise ValueError(f"{field_name} must be at least {min_val}.")
    if float_val > max_val:
        raise ValueError(f"{field_name} cannot exceed {max_val:,}.")

    return float_val
