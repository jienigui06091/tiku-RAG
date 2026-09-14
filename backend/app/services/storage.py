from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from app.services.runtime_config import get_runtime_settings


class ObjectStorageError(RuntimeError):
    pass


def store_upload(db: Session, key: str, payload: bytes, content_type: str | None) -> str:
    settings = get_runtime_settings(db)
    if settings.storage_provider.lower() == "minio":
        try:
            client = _minio_client(settings)
            bucket = _minio_bucket(settings)
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            client.put_object(
                bucket,
                key,
                BytesIO(payload),
                length=len(payload),
                content_type=content_type or "application/octet-stream",
            )
            return f"minio://{bucket}/{key}"
        except ObjectStorageError:
            raise
        except Exception as error:
            raise ObjectStorageError("MinIO upload failed") from error

    if settings.storage_provider.lower() == "local":
        target = settings.upload_dir / key.rsplit("/", maxsplit=1)[-1]
        target.write_bytes(payload)
        return str(target)

    raise ObjectStorageError(f"Unsupported storage provider: {settings.storage_provider}")


def delete_upload(db: Session, storage_path: str) -> None:
    settings = get_runtime_settings(db)
    if settings.storage_provider.lower() == "local":
        upload_dir = settings.upload_dir.resolve()
        target = Path(storage_path).resolve()
        if target.is_relative_to(upload_dir):
            target.unlink(missing_ok=True)
        return

    if settings.storage_provider.lower() == "minio" and storage_path.startswith("minio://"):
        _, _, location = storage_path.partition("minio://")
        bucket, _, key = location.partition("/")
        if bucket and key:
            _minio_client(settings).remove_object(bucket, key)


def _minio_client(settings):
    try:
        from minio import Minio
    except ImportError as error:
        raise ObjectStorageError("The minio package is required for STORAGE_PROVIDER=minio") from error

    endpoint = (settings.minio_endpoint or "").removeprefix("https://").removeprefix("http://").rstrip("/")
    if not endpoint or not settings.minio_access_key or not settings.minio_secret_key:
        raise ObjectStorageError("MINIO_ENDPOINT, MINIO_ACCESS_KEY, and MINIO_SECRET_KEY are required")
    return Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _minio_bucket(settings) -> str:
    if not settings.minio_bucket:
        raise ObjectStorageError("MINIO_BUCKET is required for STORAGE_PROVIDER=minio")
    return settings.minio_bucket
