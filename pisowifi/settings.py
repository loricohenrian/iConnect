"""
iConnect — School-Based Coin-Operated WiFi System
Django Settings
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

load_dotenv()

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Security
DEFAULT_DEV_SECRET_KEY = 'django-insecure-iConnect-dev-key-change-in-production'
SECRET_KEY = os.getenv('SECRET_KEY', DEFAULT_DEV_SECRET_KEY)
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')
ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0').split(',') if host.strip()]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'corsheaders',
    # Project apps
    'portal',
    'dashboard',
    'analytics',
    'sessions_app',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'pisowifi.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'pisowifi.wsgi.application'

# Database — SQLite for development, PostgreSQL for production
DATABASE_URL = os.getenv('DATABASE_URL', '')

if DATABASE_URL:
    # PostgreSQL
    import re
    match = re.match(
        r'postgres(?:ql)?://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)',
        DATABASE_URL
    )
    if match:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': match.group('name'),
                'USER': match.group('user'),
                'PASSWORD': match.group('password'),
                'HOST': match.group('host'),
                'PORT': match.group('port'),
            }
        }
    else:
        raise ValueError(f'Invalid DATABASE_URL format: {DATABASE_URL}')
else:
    # SQLite (default for development)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.getenv('TIMEZONE', 'Asia/Manila')
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (for reports, etc.)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DATETIME_FORMAT': '%b %d, %Y %H:%M',
}

# CORS — explicit by env, secure by default
CORS_ALLOW_ALL_ORIGINS = os.getenv('CORS_ALLOW_ALL_ORIGINS', 'False').lower() in ('true', '1', 'yes')
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(',')
    if origin.strip()
]

# Cache backend (used by login/coin/voucher/public endpoint throttling)
if os.getenv('CACHE_URL'):
    cache_location = os.getenv('CACHE_URL')
elif os.getenv('REDIS_URL'):
    cache_location = os.getenv('REDIS_URL')
else:
    cache_location = ''

if cache_location.startswith('redis://') or cache_location.startswith('rediss://'):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': cache_location,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'pisowifi-local-cache',
        }
    }

# Celery Config
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    'check-expired-sessions-every-minute': {
        'task': 'sessions_app.tasks.check_expired_sessions',
        'schedule': crontab(minute='*'),
    },
    'expire-voucher-codes-every-minute': {
        'task': 'sessions_app.tasks.expire_voucher_codes',
        'schedule': crontab(minute='*'),
    },
    'generate-daily-summary': {
        'task': 'sessions_app.tasks.generate_daily_summary',
        'schedule': crontab(hour=23, minute=55),
    },
    'generate-and-deliver-daily-report': {
        'task': 'reports.tasks.generate_and_deliver_daily_report',
        'schedule': crontab(hour=23, minute=59),
    },
    'update-active-session-bandwidth-every-minute': {
        'task': 'sessions_app.tasks.update_active_session_bandwidth',
        'schedule': crontab(minute='*'),
    },
    'enforce-dns-preauth-policy-every-minute': {
        'task': 'sessions_app.tasks.enforce_pre_auth_dns_policy',
        'schedule': crontab(minute='*'),
    },
}

# Email / report delivery settings
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '1025'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'False').lower() in ('true', '1', 'yes')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'iConnect@localhost')

PISONET_DAILY_REPORT_SEND_EMAIL = os.getenv('DAILY_REPORT_SEND_EMAIL', 'False').lower() in ('true', '1', 'yes')
PISONET_DAILY_REPORT_RECIPIENTS = [
    email.strip()
    for email in os.getenv('DAILY_REPORT_RECIPIENTS', '').split(',')
    if email.strip()
]

# iConnect Custom Settings
PISONET_GPIO_PIN = int(os.getenv('GPIO_PIN', '7'))
PISONET_GPIO_SIMULATION = os.getenv('GPIO_SIMULATION', 'True').lower() in ('true', '1', 'yes')
DEFAULT_DEVICE_API_KEY = 'iconnect-local-device-key-change-me'
PISONET_DEVICE_API_KEY = os.getenv('DEVICE_API_KEY', DEFAULT_DEVICE_API_KEY)
PISONET_ELECTRICITY_RATE = float(os.getenv('ELECTRICITY_RATE', '11.0'))
PISONET_SYSTEM_WATTAGE = 18  # ALLAN H3 (5W) + Router (10W) + Coinslot (3W)
PISONET_ESTIMATED_UTILIZATION_RATIO = float(os.getenv('ESTIMATED_UTILIZATION_RATIO', '0.35'))
PISONET_DNS_ONLY_PREAUTH = os.getenv('DNS_ONLY_PREAUTH', 'False').lower() in ('true', '1', 'yes')
PISONET_DNS_RESOLVER = os.getenv('DNS_RESOLVER', '').strip()
PISONET_PORTAL_IP = os.getenv('PORTAL_IP', '').strip()
PISONET_ENFORCE_FIREWALL_BASELINE_ON_STARTUP = os.getenv('ENFORCE_FIREWALL_BASELINE_ON_STARTUP', 'True').lower() in ('true', '1', 'yes')
PISONET_REQUIRE_FORWARD_DROP_BEFORE_SESSION = os.getenv('REQUIRE_FORWARD_DROP_BEFORE_SESSION', 'True').lower() in ('true', '1', 'yes')
PISONET_PORTAL_DEV_FLOW_ENABLED = os.getenv(
    'PORTAL_DEV_FLOW_ENABLED',
    'True' if DEBUG else 'False',
).lower() in ('true', '1', 'yes')

# Portal history passcode security
DEFAULT_HISTORY_PASSCODE = '123456'
PISONET_HISTORY_PASSCODE_ENABLED = os.getenv('HISTORY_PASSCODE_ENABLED', 'True').lower() in ('true', '1', 'yes')
PISONET_HISTORY_PASSCODE = os.getenv('HISTORY_PASSCODE', DEFAULT_HISTORY_PASSCODE).strip()

# Voucher settings
PISONET_VOUCHER_LENGTH = 6
PISONET_VOUCHER_EXPIRY_MINUTES = 5
PISONET_VOUCHER_MAX_ATTEMPTS = int(os.getenv('VOUCHER_MAX_ATTEMPTS', '8'))
PISONET_VOUCHER_WINDOW_SECONDS = int(os.getenv('VOUCHER_WINDOW_SECONDS', '300'))

# Security throttling
PISONET_LOGIN_MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
PISONET_LOGIN_WINDOW_SECONDS = int(os.getenv('LOGIN_WINDOW_SECONDS', '300'))
PISONET_COIN_MAX_REQUESTS = int(os.getenv('COIN_MAX_REQUESTS', '120'))
PISONET_COIN_WINDOW_SECONDS = int(os.getenv('COIN_WINDOW_SECONDS', '60'))
PISONET_COIN_REQUEST_WINDOW_SECONDS = int(os.getenv('COIN_REQUEST_WINDOW_SECONDS', '45'))
PISONET_COIN_REQUEST_MAX_QUEUE = int(os.getenv('COIN_REQUEST_MAX_QUEUE', '20'))
PISONET_PUBLIC_MAX_REQUESTS = int(os.getenv('PUBLIC_MAX_REQUESTS', '180'))
PISONET_PUBLIC_WINDOW_SECONDS = int(os.getenv('PUBLIC_WINDOW_SECONDS', '60'))

LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '%(asctime)s level=%(levelname)s logger=%(name)s module=%(module)s message=%(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'audit_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_DIR / 'audit.log'),
            'maxBytes': 1024 * 1024 * 5,
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'audit': {
            'handlers': ['console', 'audit_file'],
            'level': os.getenv('AUDIT_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

if not DEBUG:
    if SECRET_KEY == DEFAULT_DEV_SECRET_KEY:
        raise ImproperlyConfigured('SECRET_KEY must be set to a strong value when DEBUG=False.')
    if PISONET_DEVICE_API_KEY == DEFAULT_DEVICE_API_KEY:
        raise ImproperlyConfigured('DEVICE_API_KEY must be changed when DEBUG=False.')
    if PISONET_HISTORY_PASSCODE_ENABLED and PISONET_HISTORY_PASSCODE == DEFAULT_HISTORY_PASSCODE:
        raise ImproperlyConfigured('HISTORY_PASSCODE must be changed when DEBUG=False when history passcode is enabled.')
    if CORS_ALLOW_ALL_ORIGINS:
        raise ImproperlyConfigured('CORS_ALLOW_ALL_ORIGINS must be False when DEBUG=False.')

    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 'yes')
    if SECURE_SSL_REDIRECT:
        # TLS-enabled deployment (reverse proxy/cert in place).
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True
    else:
        # No-TLS captive portal on LAN (HTTP). Keep cookies/HSTS compatible.
        SESSION_COOKIE_SECURE = False
        CSRF_COOKIE_SECURE = False
        SECURE_HSTS_SECONDS = 0
        SECURE_HSTS_INCLUDE_SUBDOMAINS = False
        SECURE_HSTS_PRELOAD = False



