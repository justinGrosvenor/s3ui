# S3UI — Implementation Plan

This document breaks down the full implementation of S3UI from empty repo to release-ready, organized into phases. Each phase produces a working (if incomplete) application. Every task lists exactly which files are created or modified and what "done" looks like.

---

## Phase 0: Project Scaffold

Set up the repository, tooling, and an empty window that launches.

### 0.1 Repository Init

- [ ] `git init`, create `.gitignore` (Python, PyQt, macOS, Windows, IDE files)
- [ ] Create `LICENSE` (MIT)
- [ ] Create `README.md` — project name, one-liner description, "under development" badge
- [ ] Create directory structure:
  ```
  src/s3ui/
  src/s3ui/ui/
  src/s3ui/core/
  src/s3ui/models/
  src/s3ui/db/
  src/s3ui/db/migrations/
  tests/
  build/
  build/icons/
  build/scripts/
  docs/design/
  ```
- [ ] Add empty `__init__.py` to each package directory

**Done when:** `git log` shows initial commit, directory tree matches spec.

### 0.2 pyproject.toml & Dev Environment

- [ ] Create `pyproject.toml` with full metadata, dependencies, dev extras, ruff config, pytest config (as specified in spec)
- [ ] Create `src/s3ui/__init__.py` with `__version__ = "0.1.0"`
- [ ] Verify: `pip install -e ".[dev]"` succeeds
- [ ] Verify: `ruff check src/ tests/` passes (empty source, no errors)
- [ ] Verify: `pytest` runs (no tests yet, exits 0 or "no tests collected")

**Files created:** `pyproject.toml`, `src/s3ui/__init__.py`
**Done when:** A fresh venv can install the package in editable mode with all dev deps.

### 0.3 Application Entry Point

- [ ] Create `src/s3ui/app.py`:
  - `main()` function that creates `QApplication`, instantiates `MainWindow`, shows it, runs event loop
  - Single-instance check using `QLockFile` in `~/.s3ui/s3ui.lock` — if already running, bring existing window to front and exit
- [ ] Create `src/s3ui/main_window.py`:
  - Bare `QMainWindow` subclass with window title "S3UI" and minimum size 900x600
  - Empty menu bar with File > Quit
  - Empty central widget
- [ ] Create `src/s3ui/constants.py`:
  - `APP_NAME = "S3UI"`
  - `APP_DIR = Path.home() / ".s3ui"` (created on first launch if missing)
  - `DB_PATH = APP_DIR / "s3ui.db"`
  - `LOG_DIR = APP_DIR / "logs"`
  - `KEYRING_SERVICE = "s3ui"`
  - Default part sizes, concurrency limits, etc.
- [ ] Create `src/s3ui/logging_setup.py`:
  - `setup_logging()` — configures `RotatingFileHandler` to `~/.s3ui/logs/s3ui.log`
  - Max 5 MB per file, 3 backup files (20 MB total)
  - Format: `%(asctime)s %(levelname)-8s [%(name)s] %(message)s`
  - Called in `app.py` before any other initialization
- [ ] Verify: `python -m s3ui.app` launches a window, closes cleanly
- [ ] Verify: `s3ui` entry point works after `pip install -e .`
- [ ] Verify: log file created at `~/.s3ui/logs/s3ui.log` on launch

**Files created:** `app.py`, `main_window.py`, `constants.py`, `logging_setup.py`
**Done when:** The app launches, shows an empty window with a title bar and menu, creates a log file, and exits on Quit.

### 0.4 CI Pipeline

- [ ] Create `.github/workflows/ci.yml` — lint + test matrix (3 OS x 3 Python versions)
- [ ] Ensure `QT_QPA_PLATFORM=offscreen` is set for headless CI
- [ ] Create `tests/conftest.py` with a `tmp_path`-based fixture for test DB and a mock keyring fixture
- [ ] Create `tests/test_app.py` — smoke test that `main()` can be imported, `QApplication` can be constructed

**Files created:** `.github/workflows/ci.yml`, `tests/conftest.py`, `tests/test_app.py`
**Done when:** CI passes green on all 3 platforms.

---

## Phase 1: Database & Credential Storage

The foundation everything else builds on. No UI changes yet — all tested via unit tests.

### 1.1 SQLite Database Layer

- [ ] Create `src/s3ui/db/database.py`:
  - `Database` class: opens/creates `~/.s3ui/s3ui.db`
  - Enables WAL mode, foreign keys
  - Migration runner: reads `schema_version` table, applies numbered `.sql` files from `migrations/`
  - Thread-safe: uses `QMutex` for write serialization, each thread gets its own `sqlite3.Connection` via `threading.local()`
  - `execute()`, `executemany()`, `fetchone()`, `fetchall()` convenience methods
- [ ] Create `src/s3ui/db/migrations/001_initial.sql`:
  - All tables from spec: `buckets`, `bucket_snapshots`, `daily_usage`, `cost_rates`, `transfers`, `transfer_parts`, `preferences`, `schema_version`
  - `buckets` table uses `UNIQUE(name, profile)` composite constraint (not `UNIQUE(name)`) — the same bucket name accessed from different credential profiles is a valid scenario
  - All indexes
  - Seed `cost_rates` with default values
- [ ] Create `tests/test_db.py`:
  - Test: fresh DB creates all tables
  - Test: migration runs idempotently (run twice, no error)
  - Test: `schema_version` is updated
  - Test: CRUD on `preferences` table
  - Test: CRUD on `buckets` table
  - Test: same bucket name with different profiles creates two rows (composite unique)
  - Test: same bucket name with same profile is rejected (unique constraint)
  - Test: concurrent reads from two threads don't block

**Files created:** `database.py`, `001_initial.sql`, `test_db.py`
**Done when:** All DB tests pass. A fresh DB file is created with correct schema.

### 1.2 Credential Store

- [ ] Create `src/s3ui/core/credentials.py`:
  - `CredentialStore` class wrapping `keyring`
  - `list_profiles() -> list[str]`: reads profile index from keyring
  - `get_profile(name) -> Profile | None`: reads JSON from keyring entry
  - `save_profile(name, profile)`: writes JSON to keyring, updates index
  - `delete_profile(name)`: removes from keyring and index
  - `test_connection(profile) -> TestResult`: creates a temporary boto3 client, calls `list_buckets()`, returns success + bucket list or failure + plain-language error
  - `Profile` and `TestResult` dataclasses
- [ ] Create `src/s3ui/core/errors.py`:
  - `ERROR_MESSAGES: dict` mapping boto3 exception codes to plain-language strings
  - `translate_error(exception) -> tuple[str, str]`: returns `(user_message, raw_detail)`
- [ ] Create `tests/test_credentials.py`:
  - Mock `keyring.get_password` / `keyring.set_password` with a dict backend
  - Test: save profile, read it back, fields match
  - Test: list profiles returns correct names
  - Test: delete profile removes from both keyring and index
  - Test: test_connection with mocked boto3 — success case
  - Test: test_connection with mocked boto3 — invalid credentials case
  - Test: test_connection with mocked boto3 — network error case
  - Test: error translation for all entries in ERROR_MESSAGES

**Files created:** `credentials.py`, `errors.py`, `test_credentials.py`
**Done when:** All credential tests pass. Profiles round-trip through keyring correctly.

### 1.3 Preferences Helper

- [ ] Add to `database.py` (or a small `preferences.py` helper):
  - `get_pref(key, default=None) -> str | None`
  - `set_pref(key, value)`
  - Type-safe wrappers: `get_bool_pref()`, `get_int_pref()`
- [ ] Test: set and get string/bool/int preferences
- [ ] Test: missing key returns default

**Done when:** Preferences can be read and written reliably.

---

## Phase 2: S3 Client & Core Operations

The boto3 wrapper with instrumentation. Tested against moto — no UI yet.

### 2.1 S3 Client Wrapper

- [ ] Create `src/s3ui/core/s3_client.py`:
  - `S3Client` class wrapping `boto3.client('s3')`
  - Constructor takes a `Profile` and creates the boto3 client
  - Methods (all delegate to boto3 with instrumentation):
    - `list_buckets() -> list[str]`
    - `list_objects(bucket, prefix, delimiter='/') -> tuple[list[S3Item], list[str]]` — objects and common prefixes. Handles pagination internally.
    - `head_object(bucket, key) -> S3Item` — full metadata
    - `put_object(bucket, key, body)` — single-shot upload
    - `get_object(bucket, key, range=None) -> StreamingBody`
    - `delete_object(bucket, key)`
    - `delete_objects(bucket, keys: list[str])` — batch, up to 1000
    - `copy_object(src_bucket, src_key, dst_bucket, dst_key)` — always passes `MetadataDirective='COPY'` to preserve original object metadata
    - `create_multipart_upload(bucket, key) -> upload_id`
    - `upload_part(bucket, key, upload_id, part_number, body) -> etag`
    - `complete_multipart_upload(bucket, key, upload_id, parts)`
    - `abort_multipart_upload(bucket, key, upload_id)`
    - `list_parts(bucket, key, upload_id) -> list[Part]`
    - `list_multipart_uploads(bucket) -> list[MultipartUpload]` — for orphaned upload cleanup
  - Every method calls `self._cost_tracker.record_request(type)` before delegating
  - Every method wraps boto3 exceptions via `errors.translate_error()`
  - Every method logs the call at DEBUG level: method name, bucket, key (never credentials). Errors logged at ERROR level with full traceback.
- [ ] Create `S3Item` dataclass in `src/s3ui/models/s3_objects.py` (data structure only, no Qt model yet):
  ```python
  @dataclass
  class S3Item:
      name: str
      key: str
      is_prefix: bool
      size: int | None
      last_modified: datetime | None
      storage_class: str | None
      etag: str | None
  ```
- [ ] Create `tests/test_s3_client.py`:
  - Use `moto` mock S3
  - Test: list_buckets returns created buckets
  - Test: list_objects returns objects and prefixes correctly
  - Test: list_objects paginates (create >1000 objects, verify all returned)
  - Test: put_object + get_object round-trip
  - Test: delete_object removes object
  - Test: delete_objects batch removes all
  - Test: copy_object creates copy at destination
  - Test: multipart upload flow (create, upload 3 parts, complete) — verify object exists and has correct size
  - Test: abort_multipart_upload — verify upload is cleaned up
  - Test: list_parts returns uploaded parts
  - Test: head_object returns correct metadata
  - Test: error translation — attempt operation on nonexistent bucket, verify plain-language error returned

**Files created:** `s3_client.py`, `s3_objects.py` (data only), `test_s3_client.py`
**Done when:** All S3 client tests pass against moto.

### 2.2 Cost Tracker

- [ ] Create `src/s3ui/core/cost.py`:
  - `CostTracker` class:
    - Takes `Database` and `active_bucket_id` in constructor
    - `record_request(request_type, count=1)` — UPSERTs into `daily_usage`
    - `record_upload_bytes(size)` — UPSERTs bytes_uploaded
    - `record_download_bytes(size)` — UPSERTs bytes_downloaded
    - `get_daily_cost(date) -> DailyCost` — reads snapshot + usage, applies formulas
    - `get_monthly_estimate() -> float` — prorates current storage + sums MTD operations
    - `get_daily_costs(start_date, end_date) -> list[DailyCost]` — for charting
    - `get_rate(name) -> float` — reads from cost_rates table
    - `set_rate(name, rate)` — updates cost_rates table
  - `DailyCost` dataclass: `storage`, `requests`, `transfer`, `total`
- [ ] Wire `CostTracker` into `S3Client` — S3Client constructor accepts optional CostTracker, calls its methods on every operation
- [ ] Create `tests/test_cost.py`:
  - Test: record_request increments correct column
  - Test: record_request on same day accumulates
  - Test: record_request on new day creates new row
  - Test: daily cost calculation with known inputs matches expected output
  - Test: monthly estimate prorates correctly (e.g., on Jan 15, storage cost = daily * 31)
  - Test: transfer cost tiers — under 100 GB, crossing 100 GB boundary
  - Test: zero usage returns $0.00
  - Test: rate table read/write

**Files created:** `cost.py`, `test_cost.py`
**Done when:** All cost tests pass. Cost formulas produce correct results for known inputs.

---

## Phase 3: Transfer Engine

The upload/download machinery. No UI yet — tested with moto and assertions on SQLite state.

### 3.1 Transfer Engine Core

- [ ] Create `src/s3ui/core/transfers.py`:
  - `TransferEngine(QObject)`:
    - Signals: `transfer_progress`, `transfer_speed`, `transfer_status_changed`, `transfer_error`, `transfer_finished`
    - `QThreadPool` with configurable max threads
    - `enqueue(transfer_id)` — submits worker to pool
    - `pause(transfer_id)` — sets event flag, worker stops between parts
    - `resume(transfer_id)` — re-enqueues
    - `cancel(transfer_id)` — sets cancel flag, worker aborts multipart
    - `pause_all()` / `resume_all()`
    - `retry(transfer_id)` — resets retry count, re-enqueues
    - `restore_pending()` — called on startup, recovers interrupted transfers
    - Internal: `_pick_next()` — selects next queued transfer by FIFO
    - Internal: `_on_worker_finished(transfer_id)` — triggers next queued transfer

**Files created:** `transfers.py`

### 3.2 Upload Worker

- [ ] Create `src/s3ui/core/upload_worker.py`:
  - `UploadWorker(QRunnable)` with inner `Signals(QObject)`:
    - `progress(transfer_id, bytes_done, total_bytes)`
    - `speed(transfer_id, bytes_per_sec)`
    - `finished(transfer_id)`
    - `failed(transfer_id, user_msg, detail)`
  - `run()`:
    - Read transfer record from SQLite
    - Validate source file exists
    - If `total_bytes < 8MB`: single `put_object`, emit progress at 100%, done
    - If `total_bytes >= 8MB`: multipart flow:
      1. Select part size based on file size (8 MB / 64 MB / 512 MB tiers)
      2. If no `upload_id` in DB: call `create_multipart_upload`, store upload_id, create `transfer_parts` rows
      3. If `upload_id` exists (resume): call `list_parts` on S3, reconcile with DB, mark confirmed parts as completed
      4. For each pending part:
         - Check pause/cancel flags
         - Read part bytes from file at offset
         - Call `upload_part`, get etag
         - Update `transfer_parts` row: status=completed, etag
         - Update `transfers` row: transferred += part_size
         - Emit progress signal
         - Record PUT request in CostTracker
         - On failure: retry up to 3x with exponential backoff + jitter (0s, 1s+random(0–500ms), 4s+random(0–2s))
      5. After all parts: `complete_multipart_upload` with full parts list
      6. Update `transfers` status=completed
      7. Emit finished signal
    - On unrecoverable failure: update status=failed, set error_message, emit failed signal
    - On cancel: `abort_multipart_upload`, update status=cancelled
  - Speed tracking: rolling 3-second byte window, emit every 500ms

**Files created:** `upload_worker.py`

### 3.3 Download Worker

- [ ] Create `src/s3ui/core/download_worker.py`:
  - `DownloadWorker(QRunnable)` with inner `Signals(QObject)`:
    - Same signal set as UploadWorker
  - `run()`:
    - Read transfer record from SQLite
    - Validate destination directory exists
    - `head_object` to get total size and ETag
    - Temp file path: `<dest_dir>/.s3ui-download-<transfer_id>.tmp`
    - If temp file exists (resume): start from byte offset = temp file size
    - If `total_bytes < 8MB`: single `get_object`, write to temp, rename
    - If `total_bytes >= 8MB`: ranged GET loop:
      1. For each 8 MB range:
         - Check pause/cancel flags
         - `get_object` with `Range: bytes=<offset>-<offset+chunk-1>`
         - Append to temp file
         - Update `transfers` row: transferred += chunk
         - Emit progress
         - Record GET request in CostTracker
         - On failure: retry up to 3x with exponential backoff + jitter (0s, 1s+random(0–500ms), 4s+random(0–2s))
      2. Verify final file size matches expected
      3. Rename temp file to destination (atomic)
      4. Update status=completed
      5. Emit finished
    - On cancel: delete temp file, update status=cancelled

**Files created:** `download_worker.py`

### 3.4 Transfer Engine Tests

- [ ] Create `tests/test_upload_worker.py`:
  - Test: small file upload (<8 MB) — object appears on S3 with correct content
  - Test: large file upload (>8 MB) — multipart, object appears with correct size
  - Test: upload pause — worker stops, status=paused, part progress saved in DB
  - Test: upload resume — picks up from last part, completes successfully
  - Test: upload cancel — multipart aborted, no partial object on S3
  - Test: upload retry — simulate transient failure on one part, worker retries and succeeds
  - Test: upload failure — all retries exhausted, status=failed, error_message set
  - Test: upload resume after app restart — upload_id in DB, list_parts reconciliation, completes
  - Test: part size selection — correct part size for 1 MB, 10 GB, 100 GB, 1 TB files

- [ ] Create `tests/test_download_worker.py`:
  - Test: small file download — local file matches S3 content
  - Test: large file download — ranged GET, local file matches
  - Test: download pause — temp file preserved, status=paused
  - Test: download resume — starts from temp file offset, completes correctly
  - Test: download cancel — temp file deleted, status=cancelled
  - Test: download retry — transient failure, retries, succeeds
  - Test: download to nonexistent directory — fails with clear error message

- [ ] Create `tests/test_transfers.py`:
  - Test: enqueue 5 transfers with max_workers=2 — only 2 run concurrently
  - Test: transfer completes, next queued transfer starts automatically
  - Test: pause_all pauses all active, resume_all resumes
  - Test: restore_pending on startup — queued and in_progress transfers re-enqueued
  - Test: restore_pending — upload with missing source file marked as failed

**Files created:** `test_upload_worker.py`, `test_download_worker.py`, `test_transfers.py`
**Done when:** All transfer tests pass. Uploads and downloads work reliably against moto with pause/resume/cancel/retry.

---

## Phase 4: Main Window & Local Pane

First real UI. The left pane works — you can browse local files.

### 4.1 Main Window Layout

- [ ] Update `src/s3ui/main_window.py`:
  - Menu bar: File, Edit, View, Go, Bucket, Help (all menus present, most items disabled for now)
  - Toolbar: settings button (gear icon), profile selector `QComboBox` (hidden if 1 profile), spacer, bucket selector `QComboBox`
  - Central widget: `QSplitter` (horizontal) with left and right pane placeholders
  - Bottom dock: `QDockWidget` for transfer panel (empty placeholder)
  - Status bar: 4 `QLabel` segments (status, object count, total size, cost estimate)
  - Window geometry save/restore from preferences on close/open
  - Wire File > Quit to `QApplication.quit()`

**Files modified:** `main_window.py`
**Done when:** Window launches with toolbar, splitter, dock area, status bar. Menus visible but mostly non-functional.

### 4.2 Local File Pane

- [ ] Create `src/s3ui/ui/local_pane.py`:
  - `LocalPaneWidget(QWidget)`:
    - Mini toolbar: Back, Forward, search toggle, BreadcrumbBar
    - `QTreeView` with `QFileSystemModel`
    - Footer `QLabel`: item count and total size
    - Root path set from preferences or `Path.home()`
    - Back/Forward navigation stacks (list of paths, max 50)
    - Double-click directory: navigate into it
    - Double-click file: `QDesktopServices.openUrl()`
    - Columns: Name, Size, Date Modified
    - Directories sort before files
    - Hidden files hidden by default
    - Selection mode: `ExtendedSelection` (shift+click, cmd/ctrl+click)
    - Footer updates on navigation: counts items and sums sizes in current directory
  - Save last-used directory to preferences on navigation

- [ ] Create `src/s3ui/ui/breadcrumb_bar.py`:
  - `BreadcrumbBar(QWidget)`:
    - Horizontal layout of `QToolButton` segments + `QLabel` separators
    - `set_path(path: str)` — rebuilds segment buttons
    - Click segment: emits `path_clicked(str)` signal with the path up to that segment
    - Overflow: if segments exceed width, leading segments collapse into a `...` `QToolButton` with dropdown menu showing full path
    - Click whitespace to the right: enters edit mode — hide buttons, show `QLineEdit` with full path text, pre-selected
    - Enter in edit mode: emit `path_edited(str)`, exit edit mode
    - Escape or focus lost: cancel edit, restore buttons

- [ ] Wire local pane into `MainWindow` splitter (left side)
- [ ] Wire Go menu: Back, Forward, Enclosing Folder, Go to Folder...
- [ ] Wire View menu: Toggle hidden files

- [ ] Create `tests/test_local_pane.py` (pytest-qt):
  - Test: widget creates without error
  - Test: setting root path updates QFileSystemModel root
  - Test: breadcrumb bar reflects current path
  - Test: back/forward navigation history works

**Files created:** `local_pane.py`, `breadcrumb_bar.py`, `test_local_pane.py`
**Done when:** Left pane shows local files. You can navigate directories, see files with correct sizes and dates, use breadcrumbs, and use back/forward.

---

## Phase 5: S3 Pane

The right pane — browsing S3 objects. Requires Phase 1 (credentials) and Phase 2 (S3 client).

### 5.1 S3 Object Model (with Mutation API)

- [ ] Expand `src/s3ui/models/s3_objects.py`:
  - `S3ObjectModel(QAbstractTableModel)`:
    - Columns: Name, Size, Date Modified (optional: Storage Class, Full Key)
    - Internal data: `list[S3Item]`, kept sorted (prefixes first, then alphabetical)
    - `set_items(items)` — for initial load only. `beginResetModel/endResetModel`
    - Sorting: prefixes before objects, then alphabetical by name. Define a `_sort_key` function used everywhere.
    - Icons: `QFileIconProvider` for file types based on extension; folder icon for prefixes
    - Size formatting: bytes → KB → MB → GB (human-readable)
    - Date formatting: relative if < 24h ("2 hours ago"), "Jan 28" if same year, "Jan 28, 2024" otherwise
    - `get_item(row) -> S3Item` for the view to access
    - Flags: items are selectable, drag-enabled
  - **Granular mutation methods** (never call `beginResetModel` after initial load):
    - `insert_item(item: S3Item)` — uses `bisect` to find sorted position, `beginInsertRows/endInsertRows`. Preserves scroll position, selection, and any in-progress editing.
    - `remove_item(key: str)` — finds row by key, `beginRemoveRows/endRemoveRows`
    - `remove_items(keys: set[str])` — batch removal, removes highest index first to avoid shifting
    - `update_item(key: str, **fields)` — updates fields on an existing item, emits `dataChanged` for that row only (not `layoutChanged`)
    - `append_items(items: list[S3Item])` — for incremental listing population (page 2, 3, ...). `beginInsertRows` for the batch.
    - `diff_apply(new_items: list[S3Item])` — computes diff against current data: inserts new, removes missing, updates changed. Used by background revalidation. No full reset.
  - `total_size() -> int` — sum of all item sizes (for footer)
  - `item_count() -> int` — len of items list

- [ ] Create `tests/test_models.py`:
  - Test: empty model has 0 rows
  - Test: set_items with 5 items → 5 rows
  - Test: prefixes sort before objects
  - Test: size formatting for various sizes (0 bytes, 1 KB, 999 MB, 2.4 GB)
  - Test: date formatting for recent, same-year, different-year dates
  - Test: column count matches expected
  - Test: insert_item places item in correct sorted position
  - Test: insert_item on a model with existing items preserves sort order
  - Test: remove_item by key removes correct row
  - Test: remove_item with nonexistent key is a no-op
  - Test: update_item changes fields, emits dataChanged for correct index
  - Test: diff_apply — add 2 items, remove 1, update 1 — correct final state
  - Test: diff_apply with identical data emits no signals (no-op)
  - Test: append_items adds items at end, emits beginInsertRows/endInsertRows

**Files modified:** `s3_objects.py`
**Files created:** `test_models.py`

### 5.2 Listing Cache

- [ ] Create `src/s3ui/core/listing_cache.py`:
  - `CachedListing` dataclass: `prefix`, `items: list[S3Item]`, `fetched_at: float` (monotonic), `dirty: bool`, `mutation_counter: int`
  - `ListingCache`:
    - `_cache: OrderedDict[str, CachedListing]` — LRU order, max 30 entries
    - `get(prefix) -> CachedListing | None` — returns entry, promotes to MRU
    - `put(prefix, items)` — stores listing, evicts LRU if over max
    - `invalidate(prefix)` — removes one entry
    - `invalidate_all()` — clears everything (used on bucket switch or profile switch)
    - `apply_mutation(prefix, fn: Callable)` — applies a function to a cached listing's item list. Used for optimistic updates. Sets `dirty = True`, increments `mutation_counter`.
    - `is_stale(prefix) -> bool` — True if entry age > 30 seconds
    - `get_mutation_counter(prefix) -> int` — returns current counter for a prefix (captured before launching a background fetch)
    - `safe_revalidate(prefix, new_items, counter_at_fetch_start)` — applies background revalidation results safely: if `mutation_counter` has advanced since the fetch started, merges instead of replacing (preserves optimistic additions). If counter matches, does a standard diff-and-apply.
  - Thread-safe: all access behind a `threading.Lock` (cache is read/written from main thread and potentially from background revalidation callbacks)

- [ ] Create `tests/test_listing_cache.py`:
  - Test: put + get round-trip
  - Test: LRU eviction after 30 entries
  - Test: invalidate removes entry
  - Test: invalidate_all clears all
  - Test: is_stale returns False for fresh entries, True after threshold
  - Test: apply_mutation modifies cached items, sets dirty flag, increments mutation_counter
  - Test: get promotes entry to MRU (verify eviction order)
  - Test: safe_revalidate with no mutations since fetch → standard diff-apply
  - Test: safe_revalidate with mutations since fetch → merge (optimistic items preserved, external additions detected)

**Files created:** `listing_cache.py`, `test_listing_cache.py`

### 5.3 S3 Pane Widget

- [ ] Create `src/s3ui/ui/s3_pane.py`:
  - `S3PaneWidget(QWidget)`:
    - Mini toolbar: Back, Forward, search toggle, BreadcrumbBar
    - `QTableView` with `S3ObjectModel`
    - Footer `QLabel`: item count and total size
    - Owns a `ListingCache` instance
    - `set_bucket(bucket_name)` — invalidates cache, navigates to bucket root
    - `navigate_to(prefix)` — the core navigation method:
      1. **Instant (frame 0):** update breadcrumb bar, push to history stack
      2. **Cache hit:** call `model.set_items(cached.items)` immediately. If `cache.is_stale(prefix)`: launch background revalidation (see below). Done — no spinner.
      3. **Cache miss:** show inline "Loading..." state (not a modal dialog). Launch fetch worker.
      4. The user can navigate again immediately — a `_fetch_id` counter ensures only the latest navigation's response is applied.
    - **Background fetch worker:** `QThread` that calls `S3Client.list_objects()`. Emits:
      - `page_ready(prefix, items, is_first_page, fetch_id)` — for incremental loading of large listings (>1000 items). First page populates immediately, subsequent pages append.
      - `listing_complete(prefix, all_items, fetch_id)` — final signal after all pages.
    - **Background revalidation:** Same worker but the results go through `model.diff_apply(new_items)` instead of `model.set_items()`. This preserves scroll position, selection, and editing state. If the diff is empty (nothing changed), no signals are emitted — zero visual disruption.
    - **Fetch cancellation:** Navigating away while a fetch is in-flight doesn't kill the worker thread (let it finish to cache the result), but the response is ignored for UI purposes if `fetch_id` has advanced.
    - **Optimistic mutation interface** — public methods called by file operations:
      - `notify_upload_complete(key, size)` — inserts item into model + cache
      - `notify_delete_complete(keys: list[str])` — removes items from model + cache
      - `notify_rename_complete(old_key, new_key, new_name)` — updates item in model + cache
      - `notify_move_out(keys)` — removes from current listing
      - `notify_move_in(items)` — inserts into current listing if viewing the target prefix
      - `notify_new_folder(key, name)` — inserts prefix item
      - `notify_copy_complete(key, size)` — inserts item
      Each method updates both the model (for immediate UI) and the cache (so navigating away and back shows the mutation).
    - Back/Forward navigation stacks (list of prefixes, max 50)
    - Double-click prefix: navigate into it
    - Double-click file: download to temp dir + open with system app (deferred to Phase 11)
    - Error state: show error message inline in the pane if listing fails. User can click "Retry."
    - Selection mode: `ExtendedSelection`
    - Footer: "6 items, 7.9 GB" — updates on each listing and after each mutation
    - Footer during multi-page fetch: "Loading... 3,000 objects"

- [ ] Wire S3 pane into `MainWindow` splitter (right side)
- [ ] Wire bucket selector `QComboBox`:
  - On app launch: fetch bucket list from `S3Client.list_buckets()` in a worker thread, populate combo box when ready (don't block the window from appearing)
  - On selection change: `s3_pane.set_bucket(selected)` — invalidates cache, fetches new root
  - Remember last-selected bucket in preferences
- [ ] Wire profile selector:
  - Populate from `CredentialStore.list_profiles()`
  - On change: create new `S3Client` with selected profile, invalidate everything, refresh bucket list, refresh S3 pane
- [ ] Wire Go menu for S3 pane: Back, Forward
- [ ] Wire Bucket > Refresh: `cache.invalidate(current_prefix)`, re-fetch

**Files created:** `s3_pane.py`
**Done when:** Right pane shows S3 bucket contents. Navigation is instant from cache. Background revalidation fires for stale listings. Bucket selector switches buckets. Loading states are visible. The app now looks like a real dual-pane file manager.

### 5.4 S3 Pane Search / Filter

- [ ] Add filter bar to `S3PaneWidget`:
  - `QLineEdit` shown/hidden via Cmd/Ctrl+F or search button
  - On text change: filter `S3ObjectModel` — show only items where name contains filter text (case-insensitive)
  - Implemented via `QSortFilterProxyModel` wrapping `S3ObjectModel`
  - Escape: clear filter, hide bar
  - Footer: "3 of 47 items" when filtered
- [ ] Add same filter bar to `LocalPaneWidget`:
  - Same UX — filter QFileSystemModel via name filter

**Done when:** Both panes have working search/filter bars.

---

## Phase 6: Settings & Setup Wizard

The credential entry UI. After this phase, the app is usable end-to-end for browsing.

### 6.1 Setup Wizard

- [ ] Create `src/s3ui/ui/setup_wizard.py`:
  - `SetupWizard(QWizard)` with 3 pages:
  - **Page 1 (Welcome):** Static text explaining what S3UI does. "Get Started" button.
  - **Page 2 (Credentials):**
    - `QLineEdit` for Access Key ID
    - `QLineEdit` for Secret Access Key (password echo mode, eye toggle)
    - `QComboBox` for region (human-readable names mapped to region codes)
    - "Test Connection" `QPushButton`
    - On click: disable button, show spinner, call `CredentialStore.test_connection()` in a `QThread`
    - On success: green checkmark, enable "Continue"
    - On failure: red X + plain-language error message inline. If `AccessDenied`: suggest checking IAM policy.
    - "Continue" disabled until test succeeds
    - Help tooltip (ℹ icon) next to the credential fields: "Need help setting up AWS credentials?" — opens a collapsible section showing the minimum IAM policy JSON (copyable) and a brief explanation of what each permission does. Links to the README for full documentation.
  - **Page 3 (Pick a Bucket):**
    - `QListWidget` of buckets from the test_connection result
    - Single-select, first item pre-selected
    - "Finish" saves profile to keyring, saves selected bucket to preferences
  - On finish: emits `setup_complete` signal, main window initializes with the new profile/bucket

- [ ] Wire into `app.py`:
  - On launch: check `CredentialStore.list_profiles()`. If empty, show wizard instead of main window.
  - On wizard complete: show main window.

**Files created:** `setup_wizard.py`
**Done when:** First launch shows the wizard. User enters credentials, tests connection, picks a bucket, and lands in the main window with the S3 pane populated.

### 6.2 Settings Dialog

- [ ] Create `src/s3ui/ui/settings_dialog.py`:
  - `SettingsDialog(QDialog)` with `QTabWidget`:
  - **Credentials tab:**
    - List of profiles with Edit / Delete buttons
    - "Add Profile" button opens the credential form (same as wizard page 2, reuse as a sub-widget)
    - Active profile indicator
    - Delete confirmation: "Remove profile 'X'? Credentials will be removed from the system keychain."
  - **Transfers tab:**
    - Max concurrent transfers: `QSpinBox` (1–16, default 4)
    - Completed transfer retention: `QComboBox` ("Clear after session", "Keep for 24 hours", "Keep forever")
  - **Cost Rates tab:**
    - Table of rate names, current values, units
    - Editable inline (double-click value cell to edit)
    - "Reset to Defaults" button
  - **General tab:**
    - Default local directory: path + browse button
    - Show hidden files: checkbox
  - OK / Cancel / Apply buttons
  - On Apply/OK: write all changes to keyring (credentials) and preferences (everything else)

- [ ] Wire Settings gear button in toolbar to open `SettingsDialog`
- [ ] Wire menu: File > Settings (or on macOS: App menu > Preferences with Cmd+,)

**Files created:** `settings_dialog.py`
**Done when:** Settings dialog opens from toolbar/menu. All tabs work. Changes persist across app restarts.

---

## Phase 7: File Operations (No Drag-and-Drop Yet)

Upload, download, delete, rename, move, copy — via context menus and keyboard shortcuts. Drag-and-drop comes in Phase 9.

### 7.0 Operation Lock Manager

- [ ] Add `_operation_locks: dict[str, str]` to `S3PaneWidget`:
  - `acquire_lock(keys: list[str], description: str) -> bool` — attempts to lock all keys. If any key (or a parent prefix of a key) is already locked, returns False and shows a non-modal warning: "Cannot {description} — {conflicting_description} is in progress."
  - `release_lock(keys: list[str])` — releases locks for the given keys
  - Prefix locks: locking `videos/` blocks operations on any key starting with `videos/`
  - All destructive operations (delete, rename, move) must acquire locks before proceeding and release on completion (success or failure)
  - Upload completion also holds a transient lock on the destination key

**Done when:** Conflicting operations (e.g., deleting a folder while uploading into it) are blocked with a clear message.

### 7.1 Transfer Model & Panel UI

- [ ] Create `src/s3ui/models/transfer_model.py`:
  - `TransferModel(QAbstractTableModel)`:
    - Columns: Direction (↑/↓), File, Progress (%), Speed, ETA, Status
    - **Signal coalescing:** Progress signals from workers fire frequently. The model buffers updates in a `_pending_updates` dict and flushes them on a 100ms `QTimer`. This limits repaints to ~10/sec regardless of transfer count.
    - Custom delegate for progress bar column (`QStyledItemDelegate` that paints a `QProgressBar`)
    - `add_transfer(transfer_id)` — adds row, reads initial data from SQLite
    - Signal handlers: `on_progress`, `on_speed`, `on_status_changed`, `on_error`, `on_finished` — all buffer into `_pending_updates`
    - `_flush_updates()` — called by timer: emits a single `dataChanged` covering the min→max modified rows. Minimizes repaint cost.
    - ETA smoothing: `displayed_eta = 0.7 * new_eta + 0.3 * previous_eta` — prevents jittery estimates
    - Status display: "Queued", "78%", "Complete ✓", "Failed ⚠"
    - Speed display: "12.4 MB/s", "—" when not active
    - ETA display: "~47 sec", "—" when not active

- [ ] Create `src/s3ui/ui/transfer_panel.py`:
  - `TransferPanelWidget(QWidget)`:
    - Header bar: "Transfers (2 active, 1 queued)" label, spacer, Pause All / Resume All toggle button
    - `QTableView` with `TransferModel`
    - Per-row action buttons: Pause/Resume, Cancel (via delegate or overlay)
    - Failed transfers section: pinned to bottom with error message and Retry button
    - Right-click context menu: Pause, Resume, Cancel, Retry, Show in Finder/Explorer (local file)

- [ ] Wire transfer panel into `MainWindow` bottom dock
- [ ] Wire `TransferEngine` signals to `TransferModel`
- [ ] Wire Pause All / Resume All button

**Files created:** `transfer_model.py`, `transfer_panel.py`
**Done when:** Transfer panel shows in the main window. Adding a transfer programmatically shows progress, speed, ETA. Pause/Resume/Cancel buttons work.

### 7.2 Upload via Menu

- [ ] Add context menu to Local pane: "Upload to S3" — uploads selected files/folders to the S3 pane's current prefix
- [ ] Add context menu to S3 pane empty space: "Upload Files..." (native file picker), "Upload Folder..." (native directory picker)
- [ ] Implementation:
  - For each selected file: insert into `transfers` table (direction=upload, status=queued), call `TransferEngine.enqueue()`
  - For directories: walk recursively, create one transfer per file preserving relative path structure under the S3 target prefix
  - **On transfer complete:** call `s3_pane.notify_upload_complete(key, size)` — this inserts the item into the model and cache optimistically. No re-listing from S3.
  - Wire `TransferEngine.transfer_finished` signal → check if upload → call notify
- [ ] Wire CostTracker: upload bytes and PUT requests are recorded

**Done when:** You can right-click files in the local pane, choose "Upload to S3", and watch them upload in the transfer panel. Completed files appear instantly in the S3 pane without a re-fetch.

### 7.3 Download via Menu

- [ ] Add context menu to S3 pane: "Download" (downloads to local pane's current directory), "Download to..." (native directory picker)
- [ ] Implementation:
  - For each selected S3 object: insert into `transfers` table (direction=download), enqueue
  - For prefixes: recursively list all objects, create one download transfer per object, preserving structure under the local target directory
  - Name conflict handling: if local file already exists, show `NameConflictDialog` — Replace / Keep Both / Skip. "Keep Both" appends " (1)" to filename. "Apply to all" checkbox for batch operations.
- [ ] Create `src/s3ui/ui/name_conflict.py`:
  - `NameConflictDialog(QDialog)`: radio buttons for Replace / Keep Both / Skip, "Apply to all" checkbox
- [ ] Wire CostTracker: download bytes and GET requests recorded

**Files created:** `name_conflict.py`
**Done when:** You can right-click files in the S3 pane, choose "Download", and watch them download. Conflicts are handled.

### 7.4 Delete

- [ ] Create `src/s3ui/ui/confirm_delete.py`:
  - `DeleteConfirmDialog(QDialog)`:
    - Title: "Delete N files?"
    - File list (first 10 with "...and N more" if >10)
    - Total size
    - Warning text
    - Cancel / Delete buttons
- [ ] Add Delete to S3 pane context menu and keyboard shortcut (Delete / Cmd+Backspace)
- [ ] Implementation — **optimistic removal:**
  1. Show `DeleteConfirmDialog`. On confirm:
  2. **Immediately** remove items from model and cache via `s3_pane.notify_delete_complete(keys)`. The items vanish from the listing in the same frame.
  3. Fire `S3Client.delete_objects()` in a background worker.
  4. **On failure (rare):** rollback — re-insert the removed items into the model and cache, show error. The items reappear.
  - Folder delete: first list all objects under prefix (background thread with progress), show dialog with count + size, on confirm optimistic-remove + background batch delete
  - Record DELETE requests in CostTracker

**Files created:** `confirm_delete.py`
**Done when:** Delete works for files, multi-select, and folders with confirmation dialog. Deleted items disappear instantly; failures roll back.

### 7.5 Rename

- [ ] Enable inline editing in S3 pane:
  - Enter (macOS) or F2 (Win/Linux) on selected item triggers `QTableView.edit()` on the Name column
  - Custom delegate validates input: no `/`, not empty, not same as original
  - **Optimistic rename:**
    1. On commit: immediately update item in model + cache via `s3_pane.notify_rename_complete(old_key, new_key, new_name)`. The name changes in the listing instantly.
    2. Background worker: `S3Client.copy_object(old_key, new_key)` — `copy_object` always passes `MetadataDirective='COPY'` to preserve Content-Type, Cache-Control, and all custom metadata. Then `S3Client.delete_object(old_key)`.
    3. On failure: rollback — rename back to original in model + cache, show error.
  - For prefixes (folders): show progress dialog (can't be optimistic for a recursive rename of N objects), copy+delete all objects under old prefix to new prefix, update model on completion

**Done when:** You can rename files and folders inline in the S3 pane. Single-file renames are visually instant.

### 7.6 Move

- [ ] Add "Move to..." to S3 pane context menu
- [ ] Create a simple prefix picker dialog: shows bucket tree, user selects destination prefix
- [ ] Implementation — **optimistic for source listing:**
  1. Immediately remove items from current listing via `s3_pane.notify_move_out(keys)`.
  2. Background worker: `copy_object` + `delete_object` for each.
  3. On complete: if destination prefix is cached, insert items via `cache.apply_mutation()`.
  4. On failure: rollback — re-insert items into source listing, show error.
- [ ] For folders: recursive copy + batch delete (progress dialog, not optimistic — too many objects to rollback)

**Done when:** You can move files/folders between S3 prefixes via the context menu. Moved items vanish from the source instantly.

### 7.7 Copy within S3

- [ ] Implement internal clipboard:
  - Cmd/Ctrl+C on S3 selection: store keys + metadata in `MainWindow._s3_clipboard`
  - Cmd/Ctrl+V in S3 pane: for each key, `copy_object` in background, **optimistic insert** via `s3_pane.notify_copy_complete(key, size)` — copied items appear in the listing immediately
  - "Copy to..." in context menu: prefix picker dialog
  - Name conflicts: append " (copy)" suffix
  - Record PUT requests in CostTracker

**Done when:** You can copy-paste objects within S3 using keyboard shortcuts or context menu. Pasted items appear immediately.

### 7.8 New Folder

- [ ] Cmd/Ctrl+Shift+N or right-click > "New Folder" in S3 pane:
  - Insert temporary row with editable name "New Folder"
  - On commit:
    1. **Optimistic insert:** `s3_pane.notify_new_folder(key, name)` — folder appears in listing instantly
    2. Background: `put_object(key=current_prefix + name + '/', body=b'')`
    3. On failure: remove the optimistic item, show error

**Done when:** You can create new folders in S3. New folders appear instantly.

### 7.9 Get Info Dialog

- [ ] Create `src/s3ui/ui/get_info.py`:
  - `GetInfoDialog(QDialog)`:
    - File name (large, bold)
    - Size
    - Full S3 key
    - Storage class
    - Last modified (precise timestamp)
    - ETag
    - Content type
    - For prefixes: total size and object count (fetched in background)
  - Data fetched via `head_object` (for files) or recursive listing (for prefixes)
- [ ] Add "Get Info" to S3 pane context menu

**Files created:** `get_info.py`
**Done when:** Get Info dialog shows complete metadata for any S3 object or prefix.

---

## Phase 8: Bucket Statistics & Cost Dashboard

### 8.1 Stats Collector

- [ ] Create `src/s3ui/core/stats.py` (or add to s3_client):
  - `StatsCollector`:
    - `scan_bucket(bucket) -> BucketSnapshot` — runs in `QThread`
    - Paginates through all objects with `list_objects_v2`
    - Accumulates: total count, total bytes, bytes per storage class, top-10 largest
    - Emits `scan_progress(objects_counted)` per page
    - On complete: writes `bucket_snapshots` row, emits `scan_complete(snapshot)`
    - Cancellable via event flag

**Files created:** `stats.py`

### 8.2 Stats Dialog

- [ ] Create `src/s3ui/ui/stats_dialog.py`:
  - `StatsDialog(QDialog)`:
    - "Last scanned: X ago" or "Never scanned"
    - Auto-starts scan if no recent snapshot (>24 hours)
    - Scanning state: indeterminate progress bar, object count, Cancel button
    - Completed state:
      - Total objects and total size (big text)
      - Storage breakdown: horizontal bar chart (pyqtgraph `BarGraphItem`) showing bytes per storage class
      - Legends: "Standard: 246.1 GB (12,841 objects)" etc.
      - Largest files: `QTableWidget` with top 10
      - Storage over time: line chart (pyqtgraph `PlotWidget`) from `bucket_snapshots` table
    - "Refresh" button to re-scan
- [ ] Wire Bucket > Stats menu item and toolbar button

**Files modified:** `stats_dialog.py`
**Done when:** Stats dialog scans a bucket, shows breakdown and charts, persists snapshots, shows history.

### 8.3 Cost Dashboard

- [ ] Create `src/s3ui/ui/cost_dialog.py`:
  - `CostDialog(QDialog)`:
    - Monthly estimate (large text): "Estimated cost this month: $4.82"
    - Breakdown: Storage / Requests / Transfer with dollar amounts and context
    - Daily breakdown chart: stacked bar chart (pyqtgraph), last 30 days, one bar per day, segments for storage/requests/transfer
    - Per-bucket table: `QTableWidget` with columns: Bucket, Storage, Requests, Transfer, Total
    - "Export CSV" button: native save dialog, writes CSV in the format specified in spec
- [ ] Wire: Bucket > Cost Dashboard menu item
- [ ] Wire: clicking the cost estimate label in the status bar opens the Cost Dashboard

**Files modified:** `cost_dialog.py`
**Done when:** Cost dashboard shows monthly estimate, daily chart, per-bucket breakdown, and CSV export works.

### 8.4 Status Bar Cost Display

- [ ] Wire status bar cost label:
  - On app launch and after each S3 operation: call `CostTracker.get_monthly_estimate()`, update label text
  - Format: "Est. $X.XX/mo"
  - Make label clickable (or use a flat `QToolButton`): opens Cost Dashboard

**Done when:** Status bar shows live cost estimate that updates as you use the app.

---

## Phase 9: Drag and Drop

This phase adds drag-and-drop between panes and from the OS.

### 9.1 Local Pane → S3 Pane (Upload)

- [ ] Enable drag on `LocalPaneWidget.QTreeView`:
  - `setDragEnabled(True)`
  - Drag provides `QMimeData` with `text/uri-list` (local file URLs)
- [ ] Enable drop on `S3PaneWidget.QTableView`:
  - `setAcceptDrops(True)`, `setDropIndicatorShown(True)`
  - Override `dragEnterEvent`, `dragMoveEvent`, `dropEvent`
  - `dragEnterEvent`: accept if mime data contains `text/uri-list`
  - `dragMoveEvent`: highlight the current prefix as drop target
  - `dropEvent`: extract file paths from URLs, create upload transfers for each, enqueue

### 9.2 S3 Pane → Local Pane (Download)

- [ ] Enable drag on `S3PaneWidget.QTableView`:
  - Custom `QMimeData` with a custom mime type `application/x-s3ui-keys` containing JSON-encoded S3 keys
  - Also include `text/plain` with the keys for clipboard interop
- [ ] Enable drop on `LocalPaneWidget.QTreeView`:
  - Accept drops with `application/x-s3ui-keys` mime type
  - On drop: create download transfers to the local pane's current directory

### 9.3 Within S3 Pane (Move)

- [ ] Enable internal drag-and-drop in `S3PaneWidget`:
  - Dragging objects onto a prefix row = move to that prefix
  - `dropEvent`: determine target prefix from drop row, execute move (copy+delete)

### 9.4 OS File Manager → S3 Pane (External Upload)

- [ ] S3 pane also accepts drops from the OS file manager:
  - Same `text/uri-list` handling as Local→S3 drops
  - Works because the OS file manager provides `text/uri-list` by default

### 9.5 Drag and Drop Tests

- [ ] Create `tests/test_drag_drop.py` (pytest-qt):
  - Test: simulated drop on S3 pane creates upload transfers
  - Test: simulated drop on local pane creates download transfers
  - Test: internal S3 drop creates move operations
  - Test: drop of multiple files creates correct number of transfers

**Files created:** `test_drag_drop.py`
**Done when:** All four drag-and-drop directions work. Files can be dragged between panes and from the OS.

---

## Phase 10: Transfer Persistence & Notifications

### 10.1 Transfer Persistence Across Restarts

- [ ] Wire `TransferEngine.restore_pending()` in `app.py` after main window initialization:
  - Recover queued, in_progress, and paused transfers
  - Validate source/destination files still exist
  - Re-populate TransferModel
  - Start processing queue
- [ ] Test: launch app, start upload, force-quit, relaunch — upload resumes

### 10.1b Orphaned Multipart Upload Cleanup

- [ ] Add `cleanup_orphaned_uploads(bucket)` to `S3Client` or `TransferEngine`:
  - Call `list_multipart_uploads()` on active bucket
  - For each in-progress upload on S3: check if `upload_id` matches a known transfer in SQLite
  - If unknown and older than 24 hours: `abort_multipart_upload()` to free storage
  - If unknown but <24 hours: skip (may belong to another tool)
  - Log each abort at INFO level
- [ ] Wire into startup sequence: runs after `restore_pending()` in a low-priority background thread
- [ ] Test: create orphaned multipart upload via moto (>24h old), launch app, verify it gets cleaned up
- [ ] Test: recent orphaned upload (<24h) is left alone

### 10.2 System Notifications

- [ ] Add `QSystemTrayIcon` to `MainWindow` (optional tray icon — mainly for notifications):
  - On large transfer complete (>100 MB) while app is not foreground: `tray_icon.showMessage("Upload complete", filename)`
- [ ] Test: notification fires on background transfer completion

### 10.3 Upload → S3 Pane Optimistic Updates

- [ ] Wire `TransferEngine.transfer_finished` → `MainWindow`:
  - If upload: call `s3_pane.notify_upload_complete(key, size)` — item appears in listing instantly, no re-fetch
  - If download: no-op. `QFileSystemModel` auto-detects new local files via its built-in file watcher.
- [ ] Verify: upload 10 files in sequence, all appear in the S3 pane one by one as they complete, zero `list_objects_v2` calls during the process

**Done when:** Transfers survive app restarts. System notifications fire. Upload completions update the S3 pane optimistically.

---

## Phase 11: Polish & Edge Cases

### 11.1 Error Handling Polish

- [ ] Audit every S3 call — ensure all exceptions are caught and translated via `errors.py`
- [ ] Add "Show Details" expander to error dialogs (raw traceback behind a toggle)
- [ ] Network loss detection: if an S3 call fails with a connection error, show a non-modal banner at the top of the S3 pane: "Connection lost. Retrying..." Auto-dismiss when connection is restored.
- [ ] Credential expiration: if a 403 is received mid-session, show a dialog prompting to update credentials in Settings
- [ ] Permission denial handling: if any S3 call returns `AccessDenied`, disable the corresponding UI action (e.g., grey out Delete menu item) and show tooltip explaining the missing permission
- [ ] Add Help > "Show Log File" menu item: opens `~/.s3ui/logs/` in the system file manager via `QDesktopServices.openUrl()`

### 11.2 Responsiveness Audit

Verify every guarantee from the spec's "UI Responsiveness" section:

- [ ] **No main-thread blocking:** Profile the app under load (multiple active transfers + navigating the S3 pane). Use `QElapsedTimer` or Python `cProfile` to verify no single main-thread operation exceeds 50ms. Common violations to check:
  - SQLite writes on the main thread (should be fast with WAL, but verify)
  - `QFileIconProvider` for unusual file extensions (should be cached by Qt)
  - `QSortFilterProxyModel.invalidateFilter()` on large listings
- [ ] **Navigation latency:** Time the path from double-click on a folder to the first row appearing in the table. Cache hit must be <16ms. Cache miss must show spinner within 16ms (listing populates on network response).
- [ ] **Transfer panel smoothness:** With 10 concurrent transfers, the progress bars should update smoothly at ~10fps (via the 100ms coalesce timer). No visible stutter when scrolling the transfer list.
- [ ] **Optimistic operation latency:** Time the path from "Delete confirm" click to items disappearing from the listing. Must be <16ms (single-frame).
- [ ] **Incremental listing:** Navigate to a prefix with 5,000+ objects. The first 1,000 should appear within 300ms (first page). Remaining items stream in page by page. The UI must remain interactive during the streaming — user can scroll, click, even navigate away.
- [ ] **Background revalidation:** Navigate to a cached-but-stale prefix. Verify that the cached data shows immediately, and the background refresh applies as a diff (no full-model reset, scroll position preserved, selection preserved).
- [ ] **Filter performance:** Open filter bar on a listing with 10,000+ items. Type a character. Verify the filter applies in <50ms (no perceptible delay between keystroke and filtered results).
- [ ] **Drag-and-drop feedback:** Drag a file over the S3 pane. The drop highlight must appear within one frame of the cursor entering the drop zone. No lag.

### 11.3 Large Bucket Performance

- [ ] Test S3 pane with 10,000+ objects in a single prefix:
  - Ensure all pages are fetched and appended incrementally
  - Ensure UI remains interactive during the multi-page fetch
  - Footer shows "Loading... 3,000 objects" during fetch, final count when done
- [ ] Test bucket scan with 100,000+ objects — verify it completes, progress updates are smooth, dialog remains responsive

### 11.4 Keyboard Navigation

- [ ] Tab between local and S3 panes
- [ ] Ctrl+1 / Ctrl+2 focus switching
- [ ] Arrow keys navigate items in both panes
- [ ] Enter on a folder navigates into it
- [ ] Backspace / Alt+Up goes to parent folder
- [ ] Full keyboard shortcut audit against the spec table — verify all shortcuts work on all platforms

### 11.5 Window State

- [ ] Splitter position saved/restored in preferences
- [ ] Transfer panel collapsed state saved/restored
- [ ] Window size and position saved/restored
- [ ] Column widths saved/restored per pane
- [ ] Last-used local directory and S3 prefix restored on launch

### 11.6 Platform Behavior Audit

- [ ] macOS: verify Cmd shortcuts, global menu bar position, Cmd+Q quits, Cmd+, opens settings
- [ ] Windows: verify Ctrl shortcuts, in-window menu bar, proper DPI scaling
- [ ] Linux: verify Ctrl shortcuts, in-window menu bar, file icons from theme, Secret Service keyring fallback warning

### 11.7 Double-Click to Open S3 File

- [ ] Double-click file in S3 pane:
  - Download to a temp directory (`~/.s3ui/temp/`)
  - Open with system default app via `QDesktopServices.openUrl()`
  - Small files (<10 MB): download inline (blocking with progress dialog)
  - Large files (>10 MB): enqueue as a normal download, open when complete
  - Clean up temp files on app exit

**Done when:** All edge cases handled. The app feels solid on all three platforms.

---

## Phase 12: Packaging & Build Scripts

### 12.1 App Icons

- [ ] Create source SVG icon (or commission one): simple, recognizable, works at 16px and 1024px
- [ ] Create `build/generate-icons.sh`: generates .icns, .ico, .svg, .png from source
- [ ] Generate all icon files into `build/icons/`

### 12.2 PyInstaller Spec

- [ ] Create `build/s3ui.spec` (as detailed in spec doc):
  - Hidden imports for keyring backends
  - Data files: include `db/migrations/`
  - Exclude: tkinter, matplotlib, numpy
  - macOS: BUNDLE with Info.plist, icon
  - console=False
- [ ] Test: `pyinstaller build/s3ui.spec` produces a working app on each platform

### 12.3 macOS Packaging

- [ ] Create `build/scripts/sign-macos.sh`:
  - Code sign .app bundle with Developer ID
  - Create .dmg
  - Sign .dmg
  - Submit for notarization
  - Staple notarization ticket
- [ ] Create `build/entitlements.plist`
- [ ] Test: .dmg opens on a clean Mac, app launches, Gatekeeper doesn't block (if signed)
- [ ] Test: unsigned fallback — right-click > Open works

### 12.4 Windows Packaging

- [ ] Create `build/installer.nsi` (NSIS script as specified)
- [ ] Test: installer creates Start Menu shortcut, desktop shortcut, uninstaller
- [ ] Test: Add/Remove Programs entry appears
- [ ] Test: uninstaller cleanly removes everything
- [ ] Create portable .zip build script
- [ ] Test: portable .zip runs without installation

### 12.5 Linux Packaging

- [ ] Create `build/scripts/make-appimage.sh`:
  - Create AppDir structure
  - Create `s3ui.desktop` file
  - Package with `appimagetool`
- [ ] Test: AppImage runs on Ubuntu 22.04, Fedora 39, Arch — clean installs with no Python pre-installed

### 12.6 CI Release Pipeline

- [ ] Create `.github/workflows/release.yml`:
  - Triggered on `v*` tags
  - Jobs: build-macos, build-windows, build-linux, publish-pypi, create-release
  - Full signing on macOS and Windows (conditional on secrets being available)
  - Upload all artifacts to GitHub Release
- [ ] Create `.github/workflows/nightly.yml`:
  - Cron: daily test run on all platforms
- [ ] Test: push a tag, verify CI builds all artifacts and creates a GitHub Release

**Done when:** Pushing a version tag produces a GitHub Release with .dmg, .exe, .zip, .AppImage, and a PyPI package. All tested on clean machines.

---

## Phase 13: PyPI & Distribution Channels

### 13.1 PyPI

- [ ] Register project on PyPI (or TestPyPI first)
- [ ] Configure trusted publishing on PyPI for the GitHub repo
- [ ] Test: `pip install s3ui` from PyPI works, `s3ui` command launches the app
- [ ] Verify: PyPI project page shows correct metadata, description, links

### 13.2 GitHub Release Polish

- [ ] Auto-generated release notes from PR titles (configured in `.github/release.yml`)
- [ ] Release template with download links table, changelog, installation instructions
- [ ] SHA256 checksums file attached to each release

### 13.3 README

- [ ] Write `README.md`:
  - Screenshot of the app in use (dual-pane with transfers)
  - Feature list
  - Installation: pip, macOS .dmg, Windows installer, Linux AppImage
  - Quick start: how to set up credentials and start browsing
  - Building from source
  - Contributing guidelines
  - License

**Done when:** The project is published on PyPI, GitHub Releases look professional, and the README gives new users everything they need.

---

## Phase 14: Future Packaging Channels (Post-Launch)

Lower priority. Each is independent and can be done in any order after v0.1.0.

### 14.1 Homebrew

- [ ] Create `homebrew-s3ui` tap repository
- [ ] Write Formula
- [ ] Test: `brew tap OWNER/s3ui && brew install s3ui` works

### 14.2 AUR

- [ ] Write PKGBUILD
- [ ] Submit to AUR
- [ ] Test: `yay -S s3ui` works

### 14.3 Flathub

- [ ] Write Flatpak manifest
- [ ] Submit to Flathub
- [ ] Test: `flatpak install s3ui` works

### 14.4 winget / Chocolatey

- [ ] Write winget manifest, submit PR to winget-pkgs
- [ ] Write Chocolatey nuspec, publish to community repo
- [ ] Test: `winget install S3UI` and `choco install s3ui` work

---

## Summary: Phase Dependencies

```
Phase 0: Scaffold
    │
    ├──► Phase 1: Database & Credentials
    │        │
    │        ├──► Phase 2: S3 Client & Cost Tracker
    │        │        │
    │        │        ├──► Phase 3: Transfer Engine
    │        │        │        │
    │        │        │        └──► Phase 7: File Operations (menus)
    │        │        │                 │
    │        │        │                 ├──► Phase 9: Drag and Drop
    │        │        │                 │
    │        │        │                 └──► Phase 10: Persistence & Notifications
    │        │        │
    │        │        └──► Phase 8: Stats & Cost Dashboard
    │        │
    │        └──► Phase 6: Settings & Setup Wizard
    │
    ├──► Phase 4: Main Window & Local Pane
    │
    └──► Phase 5: S3 Pane (depends on 1 + 2)

Phase 11: Polish (depends on all above)
    │
    └──► Phase 12: Packaging
             │
             └──► Phase 13: Publishing
                      │
                      └──► Phase 14: Future Channels
```

## Summary: What's Usable When

| After Phase | What Works |
|---|---|
| 0 | Empty window launches |
| 1 | Credentials stored securely, DB created |
| 2 | S3 operations work (CLI-level, no UI) |
| 3 | Uploads and downloads work (CLI-level, no UI) |
| 4 | Local file browser works |
| 5 | S3 browser works — **app is now a functional dual-pane browser** |
| 6 | First-run wizard, settings — **app is usable by a new user** |
| 7 | Upload, download, delete, rename, move, copy — **app is feature-complete for file management** |
| 8 | Stats and cost tracking — **app is feature-complete** |
| 9 | Drag and drop — **app feels native** |
| 10 | Transfer resume, notifications — **app is reliable** |
| 11 | Edge cases, polish — **app is solid** |
| 12 | Packaged binaries — **app is distributable** |
| 13 | PyPI, GitHub Releases — **app is public** |
| 14 | Homebrew, AUR, Flathub, winget — **app is discoverable** |
