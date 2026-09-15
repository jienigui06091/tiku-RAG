import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import SystemSetting, User
from app.schemas import LlmSettingsOut, LlmSettingsUpdate, SystemSettingsOut, SystemSettingsUpdate

SECRET_KEYS = {
    "minio_access_key",
    "minio_secret_key",
    "milvus_token",
    "embedding_api_key",
    "rerank_api_key",
    "llm_api_key",
    "ocr_api_key",
}


class RuntimeConfigError(RuntimeError):
    pass


def get_runtime_settings(db: Session) -> Settings:
    base = get_settings()
    values = {row.key: _read_value(row) for row in db.query(SystemSetting).all()}
    known = set(Settings.model_fields)
    return base.model_copy(update={key: value for key, value in values.items() if key in known})


def get_system_settings_out(db: Session) -> SystemSettingsOut:
    settings = get_runtime_settings(db)
    stored_keys = {row.key for row in db.query(SystemSetting.key).all()}
    values = {
        key: getattr(settings, key)
        for key in SystemSettingsOut.model_fields
        if not key.endswith("_configured")
    }
    for key in SECRET_KEYS:
        values[f"{key}_configured"] = key in stored_keys or bool(getattr(settings, key))
    return SystemSettingsOut.model_validate(values)


def get_llm_settings_out(db: Session) -> LlmSettingsOut:
    settings = get_runtime_settings(db)
    stored_keys = {row.key for row in db.query(SystemSetting.key).all()}
    return LlmSettingsOut(
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        llm_temperature=settings.llm_temperature,
        llm_api_key_configured="llm_api_key" in stored_keys or bool(settings.llm_api_key),
    )


def save_system_settings(db: Session, payload: SystemSettingsUpdate, actor: User) -> SystemSettingsOut:
    updates = payload.model_dump(exclude_unset=True)
    if (
        updates.get("chunk_size") is not None
        and updates.get("chunk_overlap") is not None
        and updates["chunk_overlap"] >= updates["chunk_size"]
    ):
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    current = get_runtime_settings(db)
    proposed_size = updates.get("chunk_size", current.chunk_size)
    proposed_overlap = updates.get("chunk_overlap", current.chunk_overlap)
    if proposed_overlap >= proposed_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    for key, value in updates.items():
        if key in SECRET_KEYS and not value:
            continue
        if value is None:
            continue
        row = db.get(SystemSetting, key)
        if not row:
            row = SystemSetting(key=key, value="", is_secret=key in SECRET_KEYS)
            db.add(row)
        row.is_secret = key in SECRET_KEYS
        row.value = _write_value(value, row.is_secret)
        row.updated_by_id = actor.id
    db.commit()
    return get_system_settings_out(db)


def save_llm_settings(db: Session, payload: LlmSettingsUpdate, actor: User) -> LlmSettingsOut:
    updates = SystemSettingsUpdate(**payload.model_dump(exclude_unset=True))
    save_system_settings(db, updates, actor)
    return get_llm_settings_out(db)


def _read_value(row: SystemSetting) -> Any:
    value = _decrypt(row.value) if row.is_secret else row.value
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeConfigError(f"Invalid persisted setting: {row.key}") from error


def _write_value(value: Any, is_secret: bool) -> str:
    serialized = json.dumps(value)
    return _encrypt(serialized) if is_secret else serialized


def _fernet() -> Fernet:
    key = get_settings().settings_encryption_key
    if not key:
        raise RuntimeConfigError("SETTINGS_ENCRYPTION_KEY is required before saving secrets in the admin UI")
    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise RuntimeConfigError("SETTINGS_ENCRYPTION_KEY must be a valid Fernet key") from error


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeConfigError("Unable to decrypt persisted secret settings") from error
