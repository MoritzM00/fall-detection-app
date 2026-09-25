# Managed media retention

The application keeps uploaded media and prepared frame bundles by default. Maintenance is optional. Preview candidates first:

```sh
.venv/bin/python scripts/prune_media.py
```

The command reads `FALL_DETECTION_DATA_DIR` and `FALL_DETECTION_DATABASE_PATH` using the same settings as the API. Its default minimum age is 168 hours. To choose a longer age and then remove exactly the resources that remain eligible at apply time:

```sh
.venv/bin/python scripts/prune_media.py --min-age-hours 720
.venv/bin/python scripts/prune_media.py --min-age-hours 720 --apply
```

Only direct, regular, UUID-named upload files in `media/` and hash-named prepared bundles in `prepared/` are considered. Every file in a bundle, the bundle directory, and the upload database record must be older than the minimum age. The command protects resources referenced by **any** retained job, regardless of state, including failed jobs that can be retried. A fresh or unrecognized bundle prevents removal of a source upload it might use. Symlinks, outside-root paths, dataset videos, and unrecognized files are never removed. The maintenance lock coordinates with API uploads, job creation, and preparation; the database write transaction prevents job references being added during apply. Candidates are checked again before removal.

Applying removal deletes unreferenced prepared bundles and uploaded video records with their media files. It does not delete jobs or predictions. Deleting run history separately removes its protection: a later prune can then delete that run's source and prepared frames once they meet the age threshold. A video record without a retained job disappears from video history after removal; the underlying frames and upload cannot be recovered through this application. Back up the data directory and database according to your normal policy before applying cleanup. A file deletion interrupted after its database record is removed can leave an orphan upload, which a later run can remove. The command does not touch `omnifall/` or other datasets.

Run the command against a stopped application if any process accesses managed media without the repository's storage lock. The maintenance command itself does not run automatically. Its test suite uses disposable storage fixtures; no user media was cleaned during development.
