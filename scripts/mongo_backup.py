"""Write, verify, and restore encrypted MongoDB Database Tools archives."""

import argparse
import gzip
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from bson import BSON
from dotenv import load_dotenv

_ARCHIVE_MAGIC = 0x8199E26D.to_bytes(4, "little")


class BackupError(Exception):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackupError(f"Set {name}.")
    return value


def settings_file(name: str) -> Path:
    path = Path(required(name)).expanduser().resolve()
    if not path.is_file():
        raise BackupError(f"{name} must name an existing file.")
    return path


def database_name() -> str:
    name = required("BACKUP_DATABASE")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,38}", name) or name in {"admin", "local", "config"}:
        raise BackupError("BACKUP_DATABASE must be an application database name.")
    return name


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        value = 0
    if value < 1:
        raise BackupError(f"{name} must be a positive integer.")
    return value


def run_pipe(producer: list[str], consumer: list[str], *, operation: str) -> None:
    """Pass binary data through an OS pipe and check both process exit statuses."""
    timeout = positive_int("BACKUP_TIMEOUT_SECONDS", 1800)
    with subprocess.Popen(
        producer, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as first:
        try:
            with subprocess.Popen(
                consumer, stdin=first.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ) as second:
                first.stdout.close()
                try:
                    second_result = second.wait(timeout=timeout)
                    first_result = first.wait(timeout=timeout)
                    if first_result or second_result:
                        raise BackupError(f"{operation} failed (exit codes {first_result}, {second_result}).")
                finally:
                    if second.poll() is None:
                        second.kill()
        finally:
            if first.poll() is None:
                first.kill()


def decrypt_command(path: Path) -> list[str]:
    return ["age", "--decrypt", "--identity", str(settings_file("BACKUP_AGE_IDENTITY_FILE")), str(path)]


def verify_archive(path: Path) -> None:
    run_pipe(
        decrypt_command(path),
        [sys.executable, str(Path(__file__).resolve()), "_verify_stream"],
        operation="Archive authentication and verification",
    )


def bson_or_terminator(archive):
    prefix = archive.read(4)
    if prefix == b"\xff" * 4:
        return None
    size = int.from_bytes(prefix, "little")
    if len(prefix) != 4 or not 5 <= size <= 16 * 1024 * 1024:
        raise BackupError("Invalid BSON in archive prelude.")
    return BSON(prefix + archive.read(size - 4)).decode()


def verify_stream() -> None:
    with gzip.GzipFile(fileobj=sys.stdin.buffer) as archive:
        if archive.read(4) != _ARCHIVE_MAGIC or not bson_or_terminator(archive):
            raise BackupError("The archive header is invalid.")
        expected = os.environ.get("BACKUP_DATABASE")
        collections = set()
        while metadata := bson_or_terminator(archive):
            if expected and metadata["db"] != expected:
                raise BackupError("Archive database differs from BACKUP_DATABASE.")
            collections.add(metadata["collection"])
        if not collections or ("fs.files" in collections) != ("fs.chunks" in collections):
            raise BackupError("Expected application collections or complete GridFS collections are missing.")
        while archive.read(1024 * 1024):
            pass


def create_backup() -> None:
    name = database_name()
    path = Path(required("BACKUP_PATH")).expanduser().resolve()
    if not path.is_dir():
        raise BackupError("BACKUP_PATH must be an existing approved directory.")
    keep_days = positive_int("BACKUP_RETENTION_DAYS", 56)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{name}-{timestamp}-{uuid4().hex[:8]}.archive.gz.age"
    partial = path / f".{filename}.partial"
    destination = path / filename
    try:
        run_pipe(
            ["mongodump", "--config", str(settings_file("BACKUP_MONGO_CONFIG")),
             "--db", name, "--archive", "--gzip", "--numParallelCollections", "1"],
            ["age", "--recipients-file", str(settings_file("BACKUP_AGE_RECIPIENTS_FILE")),
             "--output", str(partial)],
            operation="Database dump and encryption",
        )
        verify_archive(partial)
        partial.rename(destination)
    finally:
        partial.unlink(missing_ok=True)

    cutoff = now - timedelta(days=keep_days)
    pattern = re.compile(rf"{re.escape(name)}-(\d{{8}}T\d{{6}}Z)-[0-9a-f]{{8}}\.archive\.gz\.age")
    for backup in path.iterdir():
        match = pattern.fullmatch(backup.name)
        if match and backup.is_file() and not backup.is_symlink():
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if created < cutoff:
                backup.unlink()
    print(f"Encrypted backup verified and saved as {destination.name}.")


def restore_backup(path: Path, *, restore_indexes: bool) -> None:
    name = database_name()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    isolated_name = f"restore_{timestamp}_{uuid4().hex[:8]}"
    restore_config = settings_file("BACKUP_RESTORE_MONGO_CONFIG")
    if os.environ.get("BACKUP_MONGO_CONFIG") and restore_config == settings_file("BACKUP_MONGO_CONFIG"):
        raise BackupError("Configure a separate restore destination.")
    verify_archive(path)
    command = [
        "mongorestore", "--config", str(restore_config), "--archive", "--gzip", "--stopOnError", "--objcheck",
        "--nsInclude", f"{name}.*", "--nsFrom", f"{name}.*", "--nsTo", f"{isolated_name}.*",
    ]
    if not restore_indexes:
        command.append("--noIndexRestore")
    run_pipe(decrypt_command(path), command, operation="Isolated database restore")
    print(f"Archive restored to {isolated_name}. Verify collection counts and GridFS before use.")


def main() -> int:
    if sys.argv[1:] == ["_verify_stream"]:
        verify_stream()
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Optional local backup settings file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup")
    commands.add_parser("verify").add_argument("archive", type=Path)
    restore = commands.add_parser("restore-test")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--restore-indexes", action="store_true", help="Only for an archive audited for TTL indexes")
    args = parser.parse_args()
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("The backup settings file does not exist")
        load_dotenv(args.env_file)

    try:
        if args.command == "backup":
            create_backup()
        elif args.command == "verify":
            verify_archive(args.archive.resolve())
            print("Encrypted archive and compressed stream verified.")
        else:
            restore_backup(args.archive.resolve(), restore_indexes=args.restore_indexes)
    except BackupError as exc:
        print(f"Database backup task failed. {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Database backup task failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
