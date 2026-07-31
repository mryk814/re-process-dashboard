from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from decision_workbench.shared_lab.config import SharedLabConfig
from decision_workbench.shared_lab.contracts import ArtifactReference
from decision_workbench.shared_lab.repository import (
    RequestIdentity,
    SharedLabRepository,
)


class ObjectStorageUnavailable(RuntimeError):
    pass


class ArtifactDigestMismatch(RuntimeError):
    pass


class ArtifactTooLarge(ValueError):
    pass


class ArtifactRegistrationFailed(RuntimeError):
    pass


class ArtifactService:
    def __init__(
        self,
        config: SharedLabConfig,
        repository: SharedLabRepository,
        client: Any | None = None,
    ):
        self.config = config
        self.repository = repository
        if client is None:
            endpoint, secure = config.minio_connection()
            client = Minio(
                endpoint,
                access_key=config.s3_access_key,
                secret_key=config.s3_secret_key,
                secure=secure,
                region=config.s3_region,
            )
        self.client = client

    @staticmethod
    def _missing(exc: S3Error) -> bool:
        return exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}

    def _exists(self, object_key: str) -> bool:
        try:
            self.client.stat_object(self.config.s3_bucket, object_key)
            return True
        except S3Error as exc:
            if self._missing(exc):
                return False
            raise ObjectStorageUnavailable("object storage is unavailable") from None
        except Exception:
            raise ObjectStorageUnavailable("object storage is unavailable") from None

    def _read_and_verify(
        self, object_key: str, expected_digest: str, expected_size: int
    ) -> bytes:
        response = None
        try:
            response = self.client.get_object(self.config.s3_bucket, object_key)
            content = response.read(self.config.max_artifact_bytes + 1)
        except Exception:
            raise ObjectStorageUnavailable("artifact cannot be read from object storage") from None
        finally:
            if response is not None:
                response.close()
                response.release_conn()
        if len(content) != expected_size or len(content) > self.config.max_artifact_bytes:
            raise ArtifactDigestMismatch("artifact size does not match registered metadata")
        if f"sha256:{sha256(content).hexdigest()}" != expected_digest:
            raise ArtifactDigestMismatch("artifact digest does not match registered metadata")
        return content

    def put_and_register(
        self,
        identity: RequestIdentity,
        *,
        artifact_id: str,
        project_id: str,
        content: bytes,
        content_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactReference:
        if len(content) > self.config.max_artifact_bytes:
            raise ArtifactTooLarge(
                f"artifact exceeds the shared-lab limit of {self.config.max_artifact_bytes} bytes"
            )
        digest = f"sha256:{sha256(content).hexdigest()}"
        object_key = f"artifacts/sha256/{digest.removeprefix('sha256:')}"
        existed = self._exists(object_key)
        if not existed:
            try:
                self.client.put_object(
                    self.config.s3_bucket,
                    object_key,
                    BytesIO(content),
                    len(content),
                    content_type=content_type,
                    metadata={"content-digest": digest},
                )
            except Exception:
                raise ObjectStorageUnavailable("artifact upload failed") from None
        try:
            self._read_and_verify(object_key, digest, len(content))
            return self.repository.register_artifact(
                identity,
                artifact_id=artifact_id,
                project_id=project_id,
                object_key=object_key,
                content_digest=digest,
                content_type=content_type,
                size_bytes=len(content),
                metadata=metadata or {},
            )
        except Exception as registration_error:
            if not existed:
                try:
                    self.client.remove_object(self.config.s3_bucket, object_key)
                except Exception:
                    raise ArtifactRegistrationFailed(
                        "artifact metadata failed and the new object needs cleanup"
                    ) from None
            raise registration_error

    def get_verified(
        self, identity: RequestIdentity, artifact_id: str
    ) -> tuple[ArtifactReference, bytes]:
        reference = self.repository.get_artifact(identity, artifact_id)
        return reference, self._read_and_verify(
            reference.object_key,
            reference.content_digest,
            reference.size_bytes,
        )
