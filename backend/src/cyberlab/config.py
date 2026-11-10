"""Runtime configuration.

All settings come from environment variables prefixed ``CYBERLAB_`` (or a local
``.env`` file). Nothing secret is ever hardcoded; secrets have no defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_registry_dir() -> Path:
    # backend/src/cyberlab/config.py -> repo root /registry
    return Path(__file__).resolve().parents[3] / "registry"


class QuotaSettings(BaseSettings):
    """Per-user resource quotas enforced at manifest validation time."""

    model_config = SettingsConfigDict(env_prefix="CYBERLAB_QUOTA_", extra="ignore")

    max_environments: int = Field(default=4, ge=1)
    max_cpu_per_environment: float = Field(default=4, gt=0)
    max_memory_mib_per_environment: int = Field(default=8192, gt=0)
    max_total_cpu: float = Field(default=6, gt=0)
    max_total_memory_mib: int = Field(default=12288, gt=0)
    max_total_storage_gib: int = Field(default=40, gt=0)
    max_ttl_hours: int = Field(default=72, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CYBERLAB_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./cyberlab-dev.db"
    # Apply pending migrations on startup. Turn off to run `cyberlab db upgrade` yourself.
    database_auto_migrate: bool = True
    registry_dir: Path = Field(default_factory=_default_registry_dir)

    # --- Authentication -------------------------------------------------
    # "jwt": verify bearer tokens issued by an OIDC/JWT provider (e.g. Supabase Auth).
    # "dev": a fixed local developer identity. Refused outside development/test.
    auth_mode: Literal["jwt", "dev"] = "jwt"
    auth_jwt_secret: SecretStr | None = None  # HS256 shared secret
    auth_jwks_url: str | None = None  # asymmetric keys (RS256/ES256)
    auth_algorithms: list[str] = ["HS256"]
    auth_audience: str | None = "authenticated"  # Supabase default audience
    auth_issuer: str | None = None
    dev_user_subject: str = "dev-user"

    # --- HTTP hardening ---------------------------------------------------
    cors_origins: list[str] = []
    max_request_bytes: int = Field(default=256 * 1024, gt=0)
    max_manifest_bytes: int = Field(default=128 * 1024, gt=0)
    max_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0)  # workspace file up/download

    # --- Snapshots -------------------------------------------------------------
    snapshot_dir: Path = Path("./cyberlab-data/snapshots")  # local snapshot store
    max_snapshot_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)  # compressed size
    max_snapshots_per_workspace: int = Field(default=10, ge=1)
    max_bundle_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0)  # import upload
    snapshot_store: Literal["local", "s3"] = "local"
    # 32-byte key (base64 or hex). When set, snapshots are encrypted (AES-256-GCM) before
    # they are stored. Required for S3. Generate one: python -m cyberlab.storage.keygen
    snapshot_encryption_key: SecretStr | None = None
    s3_bucket: str | None = None
    s3_prefix: str = "cyberlab-snapshots"
    s3_endpoint_url: str | None = None  # e.g. https://<account>.r2.cloudflarestorage.com
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None

    quotas: QuotaSettings = Field(default_factory=QuotaSettings)

    # --- Execution plane ----------------------------------------------------
    # "none": nothing can be provisioned; the API reports NOT CONNECTED.
    # "docker": a Docker Engine reached via the docker CLI (local, or remote via DOCKER_HOST).
    execution_provider: Literal["none", "docker"] = "none"
    # Which Docker Engine runs environments: unset = local socket; ssh://user@host for your
    # own server (recommended); tcp://host:2376 only with TLS (the Docker API is root-level).
    docker_host: str | None = None
    docker_tls_verify: bool = False
    docker_cert_path: str | None = None  # folder with ca.pem, cert.pem, key.pem
    # What running environments costs you. "paid" providers are refused unless allowed.
    execution_cost_class: Literal["free", "user-owned", "paid"] = "user-owned"
    allow_paid_providers: bool = False
    docker_binary: str = "docker"
    image_mirror: str | None = None  # e.g. mirror.gcr.io, replaces the docker.io/ prefix

    # --- Egress proxy (restricted-egress) -------------------------------------
    egress_proxy_image: str = "docker.io/library/python:3.13-slim"
    # Lets allow-listed names resolve to private addresses. Tests only; refused in production.
    egress_allow_private: bool = False

    # --- Resource intelligence ---------------------------------------------------
    # How often the reaper enforces idle timeouts, TTLs and ephemeral cleanup. 0 = off.
    reaper_interval_seconds: int = Field(default=60, ge=0)
    # API requests per client per minute (0 = no limit), with short bursts allowed.
    rate_limit_per_minute: int = Field(default=300, ge=0)
    rate_limit_burst: int = Field(default=60, ge=1)

    # --- Browser terminal ----------------------------------------------------
    terminal_ticket_ttl_seconds: int = Field(default=60, ge=5, le=600)
    terminal_idle_timeout_seconds: int = Field(default=900, ge=30)
    terminal_max_session_seconds: int = Field(default=7200, ge=60)
    terminal_max_sessions_per_user: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def _check_security(self) -> Settings:
        if self.auth_mode == "dev" and self.environment == "production":
            raise ValueError("CYBERLAB_AUTH_MODE=dev is not allowed in production")
        if self.auth_mode == "jwt":
            asymmetric = [a for a in self.auth_algorithms if not a.startswith("HS")]
            if "none" in (a.lower() for a in self.auth_algorithms):
                raise ValueError("JWT algorithm 'none' is never allowed")
            if asymmetric and not self.auth_jwks_url:
                raise ValueError("Asymmetric JWT algorithms require CYBERLAB_AUTH_JWKS_URL")
            if self.environment == "production" and not asymmetric and self.auth_jwt_secret is None:
                raise ValueError("CYBERLAB_AUTH_JWT_SECRET is required in production")
        if self.docker_host:
            scheme = self.docker_host.split("://", 1)[0] if "://" in self.docker_host else ""
            if scheme not in ("ssh", "unix", "tcp", "npipe"):
                raise ValueError(
                    "CYBERLAB_DOCKER_HOST must start with ssh://, unix://, tcp:// or npipe://"
                )
            if scheme == "tcp" and not self.docker_tls_verify and self.environment == "production":
                raise ValueError(
                    "an unencrypted tcp:// Docker connection gives anyone on the network root "
                    "on that host; use ssh:// or set CYBERLAB_DOCKER_TLS_VERIFY with certificates"
                )
        if self.docker_tls_verify and not self.docker_cert_path:
            raise ValueError("CYBERLAB_DOCKER_TLS_VERIFY needs CYBERLAB_DOCKER_CERT_PATH")
        if self.snapshot_store == "s3":
            if not self.s3_bucket:
                raise ValueError("CYBERLAB_S3_BUCKET is required when CYBERLAB_SNAPSHOT_STORE=s3")
            if self.snapshot_encryption_key is None:
                raise ValueError(
                    "CYBERLAB_SNAPSHOT_ENCRYPTION_KEY is required with S3 storage: "
                    "snapshots never leave this server unencrypted"
                )
        if self.snapshot_encryption_key is not None:
            from cyberlab.storage.crypto import parse_key

            parse_key(self.snapshot_encryption_key.get_secret_value())  # fail fast if malformed
        if self.environment == "production" and self.egress_allow_private:
            raise ValueError("CYBERLAB_EGRESS_ALLOW_PRIVATE is for local testing only")
        if self.environment == "production" and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origin is not allowed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
