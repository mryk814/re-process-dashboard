from __future__ import annotations

from dataclasses import dataclass, field
import os
from urllib.parse import urlparse


class SharedLabConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class SharedLabConfig:
    database_url: str = field(repr=False)
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str = field(repr=False)
    s3_secret_key: str = field(repr=False)
    s3_region: str = "us-east-1"
    allowed_workspace_id: str = "shared-lab"
    allowed_actor_ids: tuple[str, ...] = ("human-a", "human-b", "ai-reviewer")
    max_artifact_bytes: int = 16 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "SharedLabConfig":
        required = {
            "database_url": os.getenv("WORKBENCH_SHARED_DATABASE_URL")
            or os.getenv("WORKBENCH_DATABASE_URL"),
            "s3_endpoint": os.getenv("WORKBENCH_S3_ENDPOINT"),
            "s3_bucket": os.getenv("WORKBENCH_S3_BUCKET"),
            "s3_access_key": os.getenv("WORKBENCH_S3_ACCESS_KEY"),
            "s3_secret_key": os.getenv("WORKBENCH_S3_SECRET_KEY"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SharedLabConfigurationError(
                f"shared mode configuration is incomplete: {', '.join(sorted(missing))}"
            )
        return cls(**required)  # type: ignore[arg-type]

    def minio_connection(self) -> tuple[str, bool]:
        parsed = urlparse(self.s3_endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SharedLabConfigurationError(
                "WORKBENCH_S3_ENDPOINT must be an http(s) origin without credentials or a path"
            )
        return parsed.netloc, parsed.scheme == "https"
