"""Sauvegarde et restauration de la base SQLite et des fichiers associés.

Sur Streamlit Community Cloud, le disque local est éphémère : après une mise en veille ou un
redéploiement, les fichiers créés à l'exécution disparaissent. Ce module synchronise un
archive (base + images dans ``assets/``) vers un stockage durable, configuré via les secrets
ou les variables d'environnement.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

Provider = Literal["none", "file", "s3"]

_restored = False
_dirty = False
_last_sync_error: str | None = None


@dataclass(frozen=True)
class PersistenceConfig:
    provider: Provider
    backup_path: Path | None = None
    bucket: str | None = None
    object_key: str | None = None
    region: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _parse_provider(raw: str | None) -> Provider:
    value = (raw or "none").strip().lower()
    if value in {"none", "file", "s3"}:
        return value  # type: ignore[return-value]
    raise ValueError(f"Fournisseur de persistance inconnu : {raw!r} (attendu : none, file ou s3).")


def _config_from_mapping(data: dict[str, Any]) -> PersistenceConfig | None:
    provider = _parse_provider(str(data.get("provider") or data.get("type") or "none"))
    if provider == "none":
        return None

    if provider == "file":
        backup = data.get("backup_path") or data.get("path")
        if not backup:
            raise ValueError("Persistance « file » : renseignez backup_path dans [persistence].")
        return PersistenceConfig(provider="file", backup_path=Path(str(backup)).expanduser())

    bucket = data.get("bucket")
    key = data.get("object_key") or data.get("key")
    if not bucket or not key:
        raise ValueError("Persistance « s3 » : renseignez bucket et object_key (ou key).")
    return PersistenceConfig(
        provider="s3",
        bucket=str(bucket),
        object_key=str(key),
        region=str(data.get("region") or _env("AWS_DEFAULT_REGION", "auto")),
        endpoint_url=(str(data["endpoint_url"]) if data.get("endpoint_url") else _env("EDITOS_S3_ENDPOINT_URL")),
        access_key_id=str(data.get("access_key_id") or _env("AWS_ACCESS_KEY_ID")),
        secret_access_key=str(data.get("secret_access_key") or _env("AWS_SECRET_ACCESS_KEY")),
    )


def load_config() -> PersistenceConfig | None:
    """Lit la configuration depuis l'environnement, puis éventuellement les secrets Streamlit."""
    env_provider = _env("EDITOS_PERSIST_PROVIDER")
    if env_provider:
        data: dict[str, Any] = {
            "provider": env_provider,
            "backup_path": _env("EDITOS_PERSIST_BACKUP_PATH"),
            "bucket": _env("EDITOS_S3_BUCKET"),
            "key": _env("EDITOS_S3_KEY"),
            "region": _env("EDITOS_S3_REGION"),
            "endpoint_url": _env("EDITOS_S3_ENDPOINT_URL"),
            "access_key_id": _env("AWS_ACCESS_KEY_ID"),
            "secret_access_key": _env("AWS_SECRET_ACCESS_KEY"),
        }
        return _config_from_mapping(data)

    try:
        import streamlit as st

        block = dict(st.secrets.get("persistence", {}))
    except Exception:
        return None
    if not block:
        return None
    return _config_from_mapping(block)


def persistence_enabled() -> bool:
    try:
        return load_config() is not None
    except ValueError:
        return False


def last_sync_error() -> str | None:
    return _last_sync_error


def running_on_streamlit_cloud() -> bool:
    """Heuristique : l'app tourne sur Streamlit Community Cloud."""
    if _env("STREAMLIT_RUNTIME_ENVIRONMENT") == "cloud":
        return True
    if _env("IS_STREAMLIT_CLOUD", "").lower() in {"1", "true", "yes"}:
        return True
    cwd = Path.cwd()
    if str(cwd).startswith("/mount/src/"):
        return True
    return Path("/mount/src").is_dir() and cwd.is_relative_to(Path("/mount/src"))


def mark_dirty() -> None:
    global _dirty
    _dirty = True


def _db_path() -> Path:
    import database as db

    return db.DB_PATH


def checkpoint_database() -> None:
    import database as db

    path = db.DB_PATH
    if not path.exists():
        return
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _archive_members() -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []
    db_file = _db_path()
    if db_file.exists():
        members.append((db_file, "editos_insp.db"))
    if ASSETS_DIR.is_dir():
        for path in sorted(ASSETS_DIR.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                members.append((path, f"assets/{path.name}"))
    return members


def _write_archive(target: Path) -> None:
    checkpoint_database()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        for path, arcname in _archive_members():
            archive.add(path, arcname=arcname)


def export_bundle_bytes() -> bytes:
    """Archive prête à être téléchargée (base + images `assets/`)."""
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
        path = Path(handle.name)
    try:
        _write_archive(path)
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


class BackupImportError(ValueError):
    """Fichier de sauvegarde illisible ou incomplet."""


def import_bundle_bytes(data: bytes) -> None:
    """Restaure une archive téléchargée auparavant (remplace la base et les logos locaux)."""
    if not data:
        raise BackupImportError("Le fichier est vide.")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
        path = Path(handle.name)
        handle.write(data)
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = {member.name for member in archive.getmembers() if not member.isdir()}
        if "editos_insp.db" not in names:
            raise BackupImportError("Archive invalide : pas de base editos_insp.db.")
        _extract_archive(path)
    except tarfile.TarError as exc:
        raise BackupImportError(f"Archive illisible : {exc}") from exc
    finally:
        path.unlink(missing_ok=True)
    mark_dirty()


def _extract_archive(source: Path) -> None:
    if not source.exists() or source.stat().st_size == 0:
        return
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    db_file = _db_path()
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            payload = archive.extractfile(member)
            if payload is None:
                continue
            if member.name == "editos_insp.db":
                db_file.parent.mkdir(parents=True, exist_ok=True)
                db_file.write_bytes(payload.read())
            elif member.name.startswith("assets/"):
                target = BASE_DIR / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload.read())


def _s3_client(config: PersistenceConfig):
    import boto3

    return boto3.client(
        "s3",
        region_name=config.region or "auto",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
    )


def _download_from_s3(config: PersistenceConfig, destination: Path) -> bool:
    from botocore.exceptions import ClientError

    client = _s3_client(config)
    try:
        client.download_file(config.bucket, config.object_key, str(destination))
    except ClientError:
        return False
    return destination.exists() and destination.stat().st_size > 0


def _upload_to_s3(config: PersistenceConfig, source: Path) -> None:
    client = _s3_client(config)
    client.upload_file(
        str(source),
        config.bucket,
        config.object_key,
        ExtraArgs={"ContentType": "application/gzip"},
    )


def restore_if_configured() -> bool:
    """Restaure les données locales depuis la sauvegarde distante, une fois par processus."""
    global _restored
    if _restored:
        return False
    _restored = True
    try:
        config = load_config()
    except ValueError as exc:
        global _last_sync_error
        _last_sync_error = str(exc)
        return False
    if config is None:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle.tar.gz"
        if config.provider == "file":
            assert config.backup_path is not None
            if not config.backup_path.exists():
                return False
            shutil.copy2(config.backup_path, bundle)
        else:
            if not _download_from_s3(config, bundle):
                return False
        _extract_archive(bundle)
    return True


def ensure_backup() -> None:
    """Crée une première archive sur disque si la base existe déjà mais pas le fichier de sauvegarde."""
    try:
        config = load_config()
    except ValueError:
        return
    if config is None or config.provider != "file" or config.backup_path is None:
        return
    if _db_path().exists() and not config.backup_path.exists():
        sync_if_dirty(force=True)


def sync_if_dirty(*, force: bool = False) -> bool:
    """Envoie une sauvegarde si la base a été modifiée depuis la dernière synchronisation."""
    global _dirty, _last_sync_error
    if not force and not _dirty:
        return False
    if not _db_path().exists():
        _dirty = False
        return False
    try:
        config = load_config()
    except ValueError as exc:
        _last_sync_error = str(exc)
        return False
    if config is None:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "editos_bundle.tar.gz"
        try:
            _write_archive(bundle)
            if config.provider == "file":
                assert config.backup_path is not None
                config.backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundle, config.backup_path)
            else:
                _upload_to_s3(config, bundle)
        except Exception as exc:  # noqa: BLE001 — message affiché dans l'interface
            _last_sync_error = str(exc)
            return False
    _dirty = False
    _last_sync_error = None
    return True


def status_message() -> tuple[str, str] | None:
    """Message (texte, ton) pour l'interface, ou None si tout est en ordre."""
    try:
        config = load_config()
    except ValueError as exc:
        return (str(exc), "error")
    if running_on_streamlit_cloud() and config is None:
        return (
            "Sur Streamlit Community Cloud, le disque de l'application est éphémère : fiches, flux RSS "
            "et épisodes disparaissent après une mise en veille tant qu'une sauvegarde n'est pas "
            "téléchargée ou stockée (S3 dans les secrets, ou copie manuelle ci-dessous + Google Drive).",
            "warning",
        )
    if config and _last_sync_error:
        return (f"Dernière sauvegarde en échec : {_last_sync_error}", "error")
    if config and persistence_enabled():
        if config.provider == "file" and config.backup_path is not None:
            label = str(config.backup_path)
        else:
            label = f"S3 · {config.bucket}/{config.object_key}"
        return (f"Sauvegarde automatique activée ({label}).", "ok")
    return None