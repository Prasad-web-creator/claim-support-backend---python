# MongoDB storage and recovery

The upload endpoint enforces the configured per-file size before reading the multipart spool into memory. The app hashes the original bytes and stores one GridFS payload per content hash across new uploads. Each upload gets its own authorized StoredFile ID. Existing document IDs and filename-based GridFS references remain usable. PyMuPDF compresses eligible scanned PDF images at approximately 150 DPI. Digital text, searchability, significant color, signatures and existing page structure are preserved. If a rewrite fails or grows the file, the app stores the original.

Backups also include `fs.files` and `fs.chunks`. Upload metadata, policies, prescriptions and reports use permanent retention. Explicit deletion checks existing references before removing physical bytes. Scheduled cleanup checks direct and indirect references and only removes unreferenced GridFS files written by the new storage implementation after the grace period. Older files can outlive a TTL-expired metadata row, so reconcile their mappings from backups before considering them for deletion.

## Optional document retention

`DOCUMENT_RETENTION_DAYS=0` keeps documents until the user deletes them. To enable scheduled aging, set a positive number of days and allow the maintenance task to write. Maintenance checks old policies and prescriptions in bounded batches, skips documents attached to an unexpired active job, and uses the same owned-record and shared GridFS deletion path as explicit deletion. Job state, consent, reports and backups have separate retention schedules.

## Remove the old document TTL indexes

1. Use the actual application database name from `MONGODB_DATABASE` or the URI. Check the connection against a staging copy before making any change to the live database. Use a separate administrative operator with index inspection and drop access on the application database. Keep the URI in a protected local environment. A database-scoped read-write user generally has the required index privileges.
2. From `backend-python`, run the read-only inventory and retain the JSON output.

   ```powershell
   python -m scripts.retire_retention_indexes --database claimsupport
   ```

3. Confirm the inventory is limited to single-field `createdAt` TTL indexes on retained collections. Check `storedfiles.getIndexes()` while inspecting the deployment schema. The current StoredFile model supports multiple ownership records per physical `storageKey`. If a legacy deployment added a unique index to that field, reconcile that schema in staging before enabling shared deduplication.
4. Create and verify an encrypted backup. Record collection counts, GridFS file/chunk counts, and a sample of download checks. Quiesce legacy service instances when running an approved live rollout because the old code recreates its TTL indexes on startup.
5. Apply against the approved database, repeat the dry run to confirm the inventory is empty, and verify counts and downloads. The OTP expiry index remains active.

   ```powershell
   python -m scripts.retire_retention_indexes --database claimsupport --apply
   python -m scripts.retire_retention_indexes --database claimsupport
   ```

Rollback the application using a revision that leaves permanent document TTL indexes removed. Recreating those indexes would immediately make older documents eligible for deletion. If a removed index served an important sort, provision a normal `createdAt` index after checking available headroom. The JSON inventory records prior key names and expiration values for operational history. If the old retention process removed records before the migration, recover them from a backup using the isolated procedure below.

## Configure storage alerts

The backend uses the Atlas cluster-wide `atlasSize` command when available and falls back to database-scoped `dbStats`. Logs include total bytes, percentage, threshold level and measurement scope. Set `STORAGE_CAPACITY_BYTES`, `STORAGE_WARNING_THRESHOLD_PERCENT`, `STORAGE_CRITICAL_THRESHOLD_PERCENT` and `STORAGE_MONITOR_INTERVAL_HOURS` to match the cluster. Atlas calculates the M0 quota using uncompressed BSON document bytes plus index bytes. The backend check needs a running service, so configure a cluster-level alert in Atlas for coverage during Render idle periods. [Atlas storage metrics](https://www.mongodb.com/docs/atlas/free-tier-commands/) · [Atlas Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)

At the defaults, three policies and one prescription can occupy up to 20 MiB before compression. A 2–4 MiB payload estimate for four compressible scans puts roughly 100–150 initial users within a plausible range on M0. The number depends on the mix of scans and other document types, repeated uploads, BSON and index overhead, and retained analyses. Use measured logical size growth and alert headroom to plan capacity.

In the Atlas project, open **Alerts**, then **Alert Settings** and choose the **Logical Size** condition supported by M0. Configure warnings at 80% and critical alerts at 90%, or use the DevOps thresholds. Add the authorized operations contacts and confirm they receive an alert. If capacity continues growing after checking metrics and retention, DevOps can use **Database Deployments → Edit Configuration** to select a dedicated M10 tier, review its region, storage and billing, then confirm the approved change. Update app thresholds and verify reconnection, storage and uploads after the tier change. [Atlas alerts](https://www.mongodb.com/docs/atlas/configure-alerts/) · [Manage Atlas clusters](https://www.mongodb.com/docs/atlas/manage-clusters/)

## Set up a weekly encrypted backup

Atlas M0 supports a manual logical backup with MongoDB Database Tools. Its optional managed Free Cluster Backup was documented as a billed private preview with a four-snapshot daily retention window. M0 cannot dump the `admin` database, use `--oplog`, or dump users and roles. Configure backups for the application database and keep a separate operational inventory of database users, allowlists and hosting settings. Without an oplog, schedule the dump in a low-traffic period or an approved write pause when you need a consistent point-in-time view across collections. [Atlas Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/) · [Atlas managed Free Cluster Backups](https://www.mongodb.com/docs/atlas/backup/cloud-backup/free-cluster-backup/)

1. On the backup machine, install the current MongoDB Database Tools and `age`. Add `mongodump`, `mongorestore` and `age` to the scheduled account's PATH. Use the backend Python environment for the script. [MongoDB Database Tools for Windows](https://www.mongodb.com/docs/database-tools/installation/?operating-system=windows&package-type=zip) · [age installation and keys](https://github.com/FiloSottile/age)
2. In Atlas, go to **Database Access → Add New Database User** and grant the backup identity the `read` role scoped to the application database. Add the backup machine's egress address to **Network Access**. Copy the TLS connection string from **Connect → Drivers**, set the correct auth source if required, and store it in a permissions-restricted MongoDB Tools YAML configuration file. The runtime account's read-write URI remains separate. [Tools configuration](https://www.mongodb.com/docs/database-tools/mongodump/#std-option-mongodump.--config)

   ```yaml
   uri: "mongodb+srv://BACKUP_USER:REPLACE_IN_LOCAL_FILE@YOUR_ATLAS_HOST/"
   ```

3. Store the Vault/recovery copy of the private `age` identity separately. Protect the task's verification copy with filesystem permissions on an encrypted volume. Export its public recipients into a separate file. For cloud retention, sync only authenticated `.age` archives to a restricted, organization-approved folder. The identity and credential files stay outside the archive destination. The script streams plaintext between processes in memory.

   ```powershell
   age-keygen -o C:\Protected\claimsupport-age-identity.txt
   age-keygen -y C:\Protected\claimsupport-age-identity.txt | Set-Content -Encoding ascii C:\Protected\claimsupport-age-recipients.txt
   ```

4. Set the following in the backup task's protected environment file. Use actual absolute paths. Retention is applied to verified encrypted backups for the configured database after a successful backup. Review cloud-side versioning and retention separately if the folder is synced.

   | Variable | Purpose | Default |
   | --- | --- | --- |
   | `BACKUP_DATABASE` | Source application database. One to 38 letters, digits, underscores or hyphens. | Required |
   | `BACKUP_PATH` | Existing approved directory for age ciphertext. | Required for backup |
   | `BACKUP_MONGO_CONFIG` | Protected Database Tools YAML with the source URI. | Required for backup |
   | `BACKUP_RESTORE_MONGO_CONFIG` | Separate protected YAML with the isolated restore destination URI. | Required for restore |
   | `BACKUP_AGE_RECIPIENTS_FILE` | Public age recipients file. | Required for backup |
   | `BACKUP_AGE_IDENTITY_FILE` | Private age identity for verification and restore. | Required |
   | `BACKUP_RETENTION_DAYS` | Preserve verified archives for this many days. Positive integer. | 56 |
   | `BACKUP_TIMEOUT_SECONDS` | Timeout per pipeline process. Positive integer. | 1800 |

5. Run once manually under the scheduled account. In Windows Task Scheduler, create a task whose action runs the backend virtual environment's Python executable. Set **Start in** to the absolute `backend-python` directory and **Arguments** to `-m scripts.mongo_backup --env-file "C:\Protected\claimsupport-backup.env" backup`. Set a weekly trigger for the approved time. Prevent concurrent task instances and enable task history. Connect nonzero exit results from Task Scheduler to the team's operations notification or monitoring workflow. Confirm the scheduled identity reports success and the ciphertext file is current. Test the failure notification with an isolated task and invalid input.

   ```powershell
   python -m scripts.mongo_backup --env-file C:\Protected\claimsupport-backup.env backup
   python -m scripts.mongo_backup --env-file C:\Protected\claimsupport-backup.env verify "C:\ApprovedBackups\archive.archive.gz.age"
   ```

The job verifies age authentication, gzip integrity, the MongoDB archive header, the expected namespace and the GridFS collection pair before publishing an archive. A failed verification returns a nonzero exit status and does not prune existing archives. The production API has no dependency on the machine that runs the backup or offline rule development.

## Restore into an isolated database

1. Provision a separate test MongoDB server or cluster with suitable access controls, preferably using the same major MongoDB version as the archive source. Keep its credentials in `BACKUP_RESTORE_MONGO_CONFIG`. The restore operator needs write access on this isolated server. Use the approved recovery identity to access the ciphertext.
2. Restore to the fresh `restore_*` namespace created by the command. The script decrypts directly into `mongorestore`, maps the source database to an isolated name, and stops if restore reports an error. It skips archive indexes by default to keep retired TTL indexes out of the restore. Use `--restore-indexes` for an archive whose index definitions have been audited and are desired.

   ```powershell
   python -m scripts.mongo_backup --env-file C:\Protected\claimsupport-backup.env restore-test "C:\ApprovedBackups\archive.archive.gz.age"
   ```

3. Compare restored collection counts with the backup inventory and verify a representative GridFS file against its expected hash or bytes. Check session/report references, representative ownership and auth behavior, and any application index expectations before using the recovered data. If you skipped indexes, initialize Beanie indexes with the current backend configuration using the isolated database. Create GridFS's `{filename: 1, uploadDate: 1}` index in `fs.files` and unique `{files_id: 1, n: 1}` index in `fs.chunks` before app traffic. Keep live email and telemetry credentials out of the restore environment.
4. Document the snapshot time, observed restore and verification results, and any missing changes since the backup. Test an application startup against the restored copy. Keep the recovery copy isolated until DevOps authorizes a production recovery procedure. [MongoDB namespace remapping](https://www.mongodb.com/docs/database-tools/mongorestore/#std-option-mongorestore.--nsTo) · [GridFS indexes](https://www.mongodb.com/docs/manual/core/gridfs/#gridfs-indexes)

## Local verification

The storage test suite exercises size checks with and without a declared content length, native PDF text and color preservation, same-owner and cross-owner deduplication, authorized downloads, ownership enforcement, shared deletions and orphan cleanup. Run it with `CLAIMSUPPORT_TEST_MONGO_URI` set to an isolated MongoDB instance to test actual GridFS storage. Its temporary database has a random test name.

```powershell
python -m pytest -q tests/test_gridfs_storage.py tests/test_shared_file_delete.py tests/test_crud_update_keys.py
```

On the Windows verification host, a clean synthetic scan shrank from 5,206,508 to 526,136 bytes in 0.756 seconds. A heavily speckled synthetic scan shrank from 4,855,215 to 1,705,286 bytes in 0.427 seconds. Actual results vary with scan noise, color, source compression and document content. The GridFS concurrency test stored three copies of a 25-byte fixture as three ownership records over one 25-byte payload, avoiding 50 bytes of duplicate payload before counting metadata.

The retention migration was exercised on isolated MongoDB 8.0.20. Two consecutive runs removed a 30-day TTL index once and preserved the synthetic policy, ordinary timestamp index and OTP expiry index. The encrypted backup test used MongoDB Database Tools 100.20.1 and age 1.2.1. An isolated restore returned the policy and its 780,000-byte GridFS file across three chunks. Verification rejected ciphertext tampering. Render CPU and memory, Atlas storage, and the production backup scheduler require deployment-environment verification.
