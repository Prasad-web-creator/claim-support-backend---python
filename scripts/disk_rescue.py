"""
Disk-space diagnostics for the Railway MongoDB instance.

mongod refuses to build an index while free space is under
`indexBuildMinAvailableDiskSpaceMB` (default 500), so Beanie's startup index
creation raises OutOfDiskSpace and the API crash-loops. The Railway volume is
433 MB total, which is below that default -- the check can never pass there, no
matter how little data is stored. Lowering the floor is the fix.

Run from a laptop against the public connection string (MONGO_PUBLIC_URL);
the mongodb.railway.internal host only resolves inside Railway.

Usage (from backend-python/):
    .venv311/Scripts/python.exe scripts/disk_rescue.py "<MONGO_PUBLIC_URL>"
    .venv311/Scripts/python.exe scripts/disk_rescue.py "<MONGO_PUBLIC_URL>" --set-index-floor=50

Reporting is read-only. --set-index-floor changes a server parameter and lasts
only until mongod restarts; persist it in the service's start command.
"""

import sys

from pymongo import MongoClient

MB = 1024 * 1024


def human(byte_count: float) -> str:
    return f"{byte_count / MB:,.1f} MB"


def report_all_databases(client) -> None:
    """Size every database, not just the app's.

    The app database is often innocent -- on a small volume the oplog in `local`
    is usually what fills the disk, since mongod sizes it from total disk with a
    ~990 MB floor.
    """
    try:
        listing = client.admin.command("listDatabases")
    except Exception as exc:
        print(f"\n  ! listDatabases failed ({exc}); skipping cluster-wide sizes.")
        return

    print(f"\nAll databases (total {human(listing['totalSize'])}):")
    for entry in sorted(listing["databases"], key=lambda d: -d.get("sizeOnDisk", 0)):
        print(f"  {human(entry.get('sizeOnDisk', 0)):>12}  {entry['name']}")

    try:
        oplog = client.local.command("collStats", "oplog.rs")
    except Exception:
        print("\n  No oplog (standalone mongod, not a replica set).")
        return

    print("\nOplog (local.oplog.rs):")
    print(f"  storageSize {human(oplog.get('storageSize', 0))}")
    print(f"  maxSize     {human(oplog.get('maxSize', 0))}")


def report_volume(db) -> None:
    """Report the volume itself, and whether the index-build floor is reachable.

    mongod refuses to build indexes when free space drops below
    indexBuildMinAvailableDiskSpaceMB (default 500). On a volume smaller than
    that, no amount of deleting data can ever satisfy the check.
    """
    stats = db.command("dbStats")
    total = stats.get("fsTotalSize")
    used = stats.get("fsUsedSize")
    if total is None or used is None:
        print("\n  ! mongod did not report filesystem size.")
        return

    free = total - used
    print("\nVolume:")
    print(f"  total {human(total)}   used {human(used)}   free {human(free)}")

    floor_mb = 500
    try:
        param = db.client.admin.command(
            {"getParameter": 1, "indexBuildMinAvailableDiskSpaceMB": 1}
        )
        floor_mb = param["indexBuildMinAvailableDiskSpaceMB"]
    except Exception as exc:
        print(f"  ! could not read indexBuildMinAvailableDiskSpaceMB ({exc}); assuming {floor_mb}")

    print(f"  index-build floor {floor_mb} MB")
    if total < floor_mb * MB:
        print("\n  The volume is SMALLER than the floor. Deleting data cannot fix this;")
        print("  lower the floor or grow the volume.")
    elif free < floor_mb * MB:
        print(f"\n  Need {human(floor_mb * MB - free)} more free to build indexes.")
    else:
        print("\n  Enough free space for index builds.")


def report(db) -> None:
    stats = db.command("dbStats")
    print(f"\nDatabase: {db.name}")
    print(f"  dataSize    {human(stats['dataSize'])}")
    print(f"  storageSize {human(stats['storageSize'])}")
    print(f"  indexSize   {human(stats['indexSize'])}")

    rows = []
    for name in db.list_collection_names():
        try:
            cs = db.command("collStats", name)
        except Exception as exc:
            print(f"  ! collStats failed for {name}: {exc}")
            continue
        rows.append((cs.get("storageSize", 0), cs.get("count", 0), name))

    print("\n  storage      docs  collection")
    for size, count, name in sorted(rows, reverse=True):
        print(f"  {human(size):>12}  {count:>8,}  {name}")


def set_index_floor(client, megabytes: int) -> None:
    """Lower the free-space floor mongod requires before building an index.

    This is runtime-only and resets when mongod restarts. To make it stick, add
    the same setting to the Mongo service's start command on Railway:
        mongod --setParameter indexBuildMinAvailableDiskSpaceMB=<megabytes>
    """
    client.admin.command(
        {"setParameter": 1, "indexBuildMinAvailableDiskSpaceMB": megabytes}
    )
    print(f"\nindex-build floor now {megabytes} MB (until mongod restarts).")
    print("Redeploy the API; startup index creation should succeed.")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1

    uri = args[0]
    if "railway.internal" in uri:
        print("That is the internal URI and is not reachable from here.")
        print("Use MONGO_PUBLIC_URL, or enable the TCP proxy in the Railway service settings.")
        return 1

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")

    db = client.get_default_database(default="claimsupport")
    report_volume(db)
    report_all_databases(client)
    report(db)

    for arg in sys.argv[1:]:
        if arg.startswith("--set-index-floor="):
            set_index_floor(client, int(arg.split("=", 1)[1]))
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
