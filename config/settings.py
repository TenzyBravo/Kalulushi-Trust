"""Settings for the Kalulushi Trust school system. Environment-specific values come from env vars."""
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Environment variable {name} is required")
    return value


DEBUG = env("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key" if DEBUG else None)
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]
# Render sets this to the service's public hostname.
if render_host := os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(render_host)
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "core",
    "school",
    "accounts",
    "audit",
    # Keep last: it re-applies role permissions once every app's permissions exist.
    "learners",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.context.RequestContextMiddleware",
    "accounts.middleware.SessionActivityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Sign in with staff number, username or email (design §5). Being a ModelBackend,
# it also refuses a deactivated user on their next request.
AUTHENTICATION_BACKENDS = ["accounts.backends.StaffLoginBackend"]
SESSION_ENGINE = "django.contrib.sessions.backends.db"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.school",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

def _database():
    """DATABASE_URL (as hosting providers give it) wins over the separate POSTGRES_* variables."""
    if url := os.environ.get("DATABASE_URL"):
        parts = urlparse(url)
        return {
            "NAME": parts.path.lstrip("/"),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or 5432),
        }
    return {
        "NAME": env("POSTGRES_DB", "school_ms"),
        "USER": env("POSTGRES_USER", "school"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }


DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "CONN_MAX_AGE": 60, **_database()}}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lusaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SESSION_COOKIE_SECURE = env("DJANGO_SECURE_COOKIES", "true").lower() == "true"
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    # Behind the host's HTTPS proxy (e.g. Render), which sets X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
