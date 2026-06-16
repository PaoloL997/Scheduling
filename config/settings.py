"""Django settings for programmazione_produzione project."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY non impostata. Configurala nel file .env "
        "o come variabile d'ambiente."
    )

DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes", "on"}

_allowed_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ui.apps.UiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "ui.middleware.SessionTrackingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ui.context_processors.open_problems",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "it-it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "suppress_status_endpoint": {
            "()": "config.logging_filters.SuppressStatusEndpoint",
        },
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} — {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["suppress_status_endpoint"],
        },
    },
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "ui": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "src": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Application-specific paths
# ---------------------------------------------------------------------------

SERVER_DIR = os.getenv("SERVER_DIR", "")
MPP_PATH = os.getenv("MPP_PATH", str(BASE_DIR / "dataset.mpp"))
XLSX_PATH = os.getenv("XLSX_PATH", str(BASE_DIR / "jobs.xlsx"))
PENALE_MAX = os.getenv("PENALE_MAX", "5")
SOLVER_TIME_LIMIT = os.getenv("SOLVER_TIME_LIMIT", "10")
HEARTBEAT_INTERVAL_SECONDS = os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30")
HEARTBEAT_TIMEOUT_SECONDS = os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "90")
MSP_DISPATCH_RETRY_INTERVAL_SECONDS = os.getenv(
    "MSP_DISPATCH_RETRY_INTERVAL_SECONDS", "0.5"
)
MSP_DISPATCH_RETRY_MAX_WAIT_SECONDS = os.getenv(
    "MSP_DISPATCH_RETRY_MAX_WAIT_SECONDS", "5.0"
)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "512")
AUTO_IMPORT_ON_STARTUP = os.getenv("AUTO_IMPORT_ON_STARTUP", "true")

# ---------------------------------------------------------------------------
# Media files (WPS PDF uploads)
# ---------------------------------------------------------------------------

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
