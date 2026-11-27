"""Run with python -m scripts.retire_retention_indexes from backend-python."""

import argparse
import json
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.services.ttl_service import retire_retention_indexes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="Explicit database name")
    parser.add_argument("--apply", action="store_true", help="Remove the matching TTL indexes")
    args = parser.parse_args()
    if args.apply and not args.database:
        parser.error("--apply requires an explicit --database")
    load_dotenv()
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL")
    if not uri:
        parser.error("Set MONGODB_URI in a protected environment first")
    options = {"serverSelectionTimeoutMS": 10_000}
    if uri.startswith("mongodb+srv://") or "tls=true" in uri.lower():
        options["tlsCAFile"] = certifi.where()
    try:
        with MongoClient(uri, **options) as client:
            name = args.database or os.environ.get("MONGODB_DATABASE")
            database = client[name] if name else client.get_default_database(default="claimsupport")
            changes = retire_retention_indexes(database, apply=args.apply)
            print(json.dumps({"database": database.name, "apply": args.apply, "indexes": changes}, indent=2))
    except PyMongoError as exc:
        print(f"Retention migration failed ({type(exc).__name__}); re-run inventory to check progress.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
