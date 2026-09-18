# Railway MongoDB: required start-command flag

## The flag

The Mongo service on Railway must start with an explicit index-build floor:

```
docker-entrypoint.sh mongod --ipv6 --bind_ip ::,0.0.0.0 --setParameter diagnosticDataCollectionEnabled=false --setParameter indexBuildMinAvailableDiskSpaceMB=50
```

Set it in the Mongo service under Settings -> Deploy -> Custom Start Command.

## Why

mongod refuses to build an index while free disk is below
`indexBuildMinAvailableDiskSpaceMB`, which defaults to **500 MB**. The Railway
volume on the free tier is **433 MB total**, so that check can never pass there
no matter how little data is stored.

`connect_to_mongodb()` calls `init_beanie()`, which creates indexes for every
document model on startup. Without the flag, that raises:

```
pymongo.errors.OperationFailure: available disk space of 228950016 bytes is
less than required minimum of 524288000 (code 14031, OutOfDiskSpace)
```

The API then fails startup and the container restarts every few seconds.

Deleting data does not help: the whole database is about 4 MB. The other ~210 MB
in use is WiredTiger's journal and preallocated files. The constraint is the size
of the volume, not its contents.

The setting is runtime-only when applied with `setParameter`, so it must live in
the start command or it is lost on the next Mongo restart.

## Diagnosing

`scripts/disk_rescue.py` reports volume size, the live index-build floor, and
per-database and per-collection sizes. It needs the public connection string
(`MONGO_PUBLIC_URL`); `mongodb.railway.internal` only resolves inside Railway.

```
.venv311/Scripts/python.exe scripts/disk_rescue.py "<MONGO_PUBLIC_URL>"
```

Reporting is read-only. `--set-index-floor=<MB>` changes the parameter live,
which is useful to get a crash-looping service back up before editing the start
command.

## Capacity

With a 433 MB volume and `MAX_FILE_SIZE_MB` at 20, roughly eight full-size
uploads to GridFS will push free space back under the 50 MB floor -- and that
would be a genuine out-of-space condition, not a config mismatch. Moving file
storage out of GridFS to object storage, or growing the volume, is the durable
fix.
