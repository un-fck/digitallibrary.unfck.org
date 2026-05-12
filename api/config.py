from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings

SSL_CERT_PATHS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]


def _patch_ssl(url: str) -> str:
    """Append sslrootcert to a PostgreSQL URL if not already present."""
    if "sslrootcert" in url:
        return url
    for path in SSL_CERT_PATHS:
        if os.path.exists(path):
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslrootcert={path}"
    return url


class Settings(BaseSettings):
    database_url: str = ""
    api_secret_key: str = "change-me-in-production"

    # SMTP (reuse the same config as the Next.js auth emails)
    smtp_host: str = "smtp.mailbox.org"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = "noreply@documents.unfck.org"

    # Public URL for verification links
    public_url: str = "http://localhost:3000"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def db_conninfo(self) -> str:
        return _patch_ssl(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
