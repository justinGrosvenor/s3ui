# S3UI - Design Specification

## Overview

S3UI is a cross-platform, open-source desktop application built with PyQt6 that makes Amazon S3 feel like a local file system. It provides a native dual-pane file manager — local files on one side, S3 on the other — with drag-and-drop transfers, reliable large-file handling, and built-in cost tracking.

The primary design goal is that a non-technical person comfortable with Finder or Explorer should be able to use S3UI without training. No protocol pickers, no jargon, no developer-oriented UI. Enter credentials once, pick a bucket, and manage files.

**License:** MIT
**Platforms:** Windows, macOS, Linux
**Runtime:** Python 3.11+
**UI Framework:** PyQt6

---

## Why This Exists

There is no free, open-source, cross-platform S3 desktop client that provides a native file-manager experience for non-technical users. The existing landscape:

### Paid Dual-Pane with S3 (macOS only)

| App | Price | Platform | Notes |
|---|---|---|---|
| Commander One PRO | $30 | macOS | S3 requires paid upgrade; power-user UI |
| ForkLift 4 | $30 | macOS | Developer-oriented dual-pane manager |
| Transmit | $45 | macOS | Long-standing, protocol-heavy interface |

These are competent tools aimed at developers and sysadmins. They expose protocol details, ACL editors, and raw S3 metadata in ways that are confusing for a non-technical user.

### Mount-as-Drive

| App | Price | Platform | Notes |
|---|---|---|---|
| ExpanDrive | $50/yr | All | Mounts S3 as local drive |
| CloudMounter | $45 | macOS/Win | Mounts S3 as Finder/Explorer folder |
| Mountain Duck | $39 | macOS/Win | From the Cyberduck team |

The mount approach makes S3 look like a local disk, which is conceptually simple. However, it has real problems with large files (video, archives): writes go through a local cache, uploads can silently fail, there is no progress visibility, and no resume on interrupted transfers. Poor fit for a video library.

### Free / Open Source

| App | Platform | Notes |
|---|---|---|
| Cyberduck | macOS/Win | Single-pane, remote-only, developer UI. Dual-pane requested since 2008, never implemented. |
| WinSCP | Windows | Dual-pane with S3 support but Windows-only and developer-oriented. |
| Electron S3 File Manager | All | Abandoned. Basic features, no multipart resume. |
| S3 Browser | Windows | Freeware (not open source), Windows only. |
| s3gui (Flutter) | All | Early stage, limited features. |

### What Nobody Does

No existing free tool combines:
- Native OS look and feel (not "developer software")
- Dual-pane local + S3 side by side in one window
- Reliable large file transfers with visible progress and resume
- Cross-platform (Windows, macOS, Linux)
- Cost tracking and bucket analytics
- Simple enough for a non-technical user

S3UI fills this gap.

---

## Target User

The primary user is someone who:
- Is comfortable with their OS file manager (Finder, Explorer, Nautilus)
- Needs to manage files on S3 (video library, website assets, backups)
- Does not want to use a CLI, the AWS Console, or developer-oriented tools
- Wants to see what things cost without logging into AWS billing

Example use case: managing a video library stored on S3 and maintaining folder structures that serve a website, with files ranging from small HTML/CSS to multi-gigabyte video.

---

## Design Principles

1. **Look native.** Match the host OS conventions. Use system fonts, standard widget styles, platform-appropriate keyboard shortcuts (Cmd on macOS, Ctrl on Windows/Linux). The app should feel like Finder or Explorer, not like a web app or a developer tool.

2. **Hide complexity.** S3 has no real folders, just key prefixes — the user never needs to know this. Storage classes, multipart upload IDs, ETags — all hidden unless the user asks. The default view shows Name, Size, and Date Modified, just like a file manager.

3. **Transfers must be visible and reliable.** Large files are the core use case. Every transfer shows real progress, estimated time remaining, and current speed. Interrupted transfers resume automatically. Failed transfers are retried. Nothing silently fails.

4. **One-time setup.** Enter credentials, pick a bucket, start working. No config files to edit, no region selectors to understand, no IAM policy documentation to read.

5. **No telemetry, no accounts, no cloud.** The app talks to S3 and nothing else. Usage data stays in a local SQLite database. The user owns their data completely.

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         PyQt6 UI (Main Thread)                  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ LocalPane    │  │ S3Pane       │  │ TransferPanel         │ │
│  │ (QTreeView)  │  │ (QTableView) │  │ (QTableView)          │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘ │
│         │                 │                       │             │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌───────────▼───────────┐ │
│  │ LocalFSModel │  │ S3Model      │  │ TransferModel         │ │
│  │ (QFileSystem │  │ (QAbstract   │  │ (QAbstractTableModel) │ │
│  │  Model)      │  │  TableModel) │  │                       │ │
│  └──────────────┘  └──────┬───────┘  └───────────┬───────────┘ │
│                           │  signals              │  signals    │
├───────────────────────────┼───────────────────────┼─────────────┤
│                    Core Services                                │
│                                                                 │
│  ┌────────────────┐  ┌─────────────────┐  ┌──────────────────┐ │
│  │ S3Client       │  │ TransferEngine  │  │ CredentialStore  │ │
│  │                │  │                 │  │                  │ │
│  │ - list_objects │  │ - QThreadPool   │  │ - keyring read   │ │
│  │ - head_object  │  │ - upload queue  │  │ - keyring write  │ │
│  │ - delete       │  │ - download queue│  │ - profile CRUD   │ │
│  │ - copy         │  │ - pause/resume  │  │                  │ │
│  │ - initiate_mpu │  │ - retry logic   │  └────────┬─────────┘ │
│  │ - upload_part  │  │ - progress sigs │           │           │
│  │ - complete_mpu │  │                 │           │           │
│  └───────┬────────┘  └────────┬────────┘           │           │
│          │                    │                     │           │
│  ┌───────▼────────┐  ┌───────▼────────┐  ┌────────▼─────────┐ │
│  │ CostTracker    │  │ StatsCollector │  │ DB               │ │
│  │                │  │                │  │                  │ │
│  │ - intercepts   │  │ - bucket scan  │  │ - SQLite conn    │ │
│  │   all S3 calls │  │ - snapshot     │  │ - migrations     │ │
│  │ - logs to DB   │  │ - background   │  │ - WAL mode       │ │
│  │ - rate table   │  │   worker       │  │                  │ │
│  └───────┬────────┘  └───────┬────────┘  └──────────────────┘ │
└──────────┼────────────────────┼─────────────────────────────────┘
           │                    │
     ┌─────▼─────┐       ┌─────▼──────┐
     │ boto3 /   │       │ SQLite     │
     │ botocore  │       │ (~/.s3ui/  │
     │           │       │  s3ui.db)  │
     └───────────┘       └────────────┘
```

### Threading Model

The UI must never freeze. All S3 I/O happens off the main thread.

| Component | Thread | Communication |
|---|---|---|
| All PyQt6 widgets, models, menus | Main thread | Direct method calls |
| S3 listing (list_objects_v2) | QThread per request | Signal `listing_ready(prefix, items)` back to main |
| File uploads | QThreadPool workers | Signal `progress(transfer_id, bytes)` per chunk |
| File downloads | QThreadPool workers | Signal `progress(transfer_id, bytes)` per chunk |
| Bucket stats scan | Dedicated QThread | Signal `scan_progress(count)`, `scan_complete(snapshot)` |
| Cost calculation | Main thread (CPU-only, fast) | Reads from SQLite, updates status bar |
| SQLite writes | Serialized via QMutex | Any thread can write; mutex prevents contention |

#### Why QThreadPool for Transfers

Transfers are the heaviest workload. A `QThreadPool` with configurable max thread count (default 4) manages concurrency. Each transfer is a `QRunnable` subclass that:

1. Reads its state from the `transfers` SQLite table
2. Emits progress signals as chunks transfer
3. Updates SQLite on completion, failure, or pause
4. Can be cancelled via a shared `threading.Event` flag

The `TransferModel` listens for progress signals and updates the UI. Because signals cross thread boundaries via Qt's event loop, this is thread-safe without manual locking on the UI side.

#### Signal Flow Example: Upload

```
User drags file from LocalPane to S3Pane
  │
  ▼
MainWindow.handle_drop()
  │  Creates transfer record in SQLite (status='queued')
  │  Adds row to TransferModel
  ▼
TransferEngine.enqueue(transfer_id)
  │  Submits UploadWorker to QThreadPool
  ▼
UploadWorker.run()                         [worker thread]
  │  Reads transfer record from SQLite
  │  If file > 8MB: initiate multipart upload
  │    │  Store upload_id in SQLite
  │    │  For each 8MB part:
  │    │    upload_part()
  │    │    emit progress(transfer_id, bytes_so_far)
  │    │    CostTracker.record_put_request()
  │    │  complete_multipart_upload()
  │  Else: put_object() single shot
  │  Update SQLite status='completed'
  │  emit finished(transfer_id)
  ▼
TransferModel receives finished signal     [main thread]
  │  Updates row status to "Complete"
  │  Refreshes S3Pane listing for the target prefix
  ▼
CostTracker
  │  Has already counted each PUT request
  │  Updates daily_usage bytes_uploaded
  │  Status bar cost estimate refreshes
```

### Error Propagation

All exceptions from worker threads are caught within the worker, never allowed to crash the thread. The pattern:

```python
class UploadWorker(QRunnable):
    # Signals defined on a separate QObject (QRunnable can't have signals)
    class Signals(QObject):
        progress = pyqtSignal(int, int)     # transfer_id, bytes
        finished = pyqtSignal(int)          # transfer_id
        failed = pyqtSignal(int, str, str)  # transfer_id, user_msg, detail

    def run(self):
        try:
            self._do_upload()
        except ClientError as e:
            code = e.response['Error']['Code']
            user_msg = ERROR_MESSAGES.get(code, "Upload failed.")
            self.signals.failed.emit(self.transfer_id, user_msg, str(e))
        except Exception as e:
            self.signals.failed.emit(
                self.transfer_id,
                "Upload failed unexpectedly.",
                traceback.format_exc()
            )
```

The `failed` signal carries both a user-facing message and the raw traceback. The UI shows the user message; the raw detail is behind "Show Details."

### Logging Strategy

The app uses Python's `logging` module for structured debug logging to a rotating file. Logs are never shown to the user during normal operation — they exist for bug reports and developer debugging.

**Log file location:** `~/.s3ui/logs/s3ui.log`

**Configuration:**

```python
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "s3ui.log"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3                 # keep 3 rotated files (20 MB max total)

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)-8s [%(name)s] %(message)s'
    ))
    root = logging.getLogger('s3ui')
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
```

**What gets logged (at which level):**

| Level | What |
|---|---|
| DEBUG | S3 API calls (method, bucket, key — never credentials), cache hits/misses, worker lifecycle (start/finish/retry), SQLite queries |
| INFO | Transfer started/completed/failed, bucket switch, profile switch, app launch/shutdown |
| WARNING | Retry attempts, cache evictions, stale revalidation differences, orphaned temp files |
| ERROR | Unrecoverable failures (all retries exhausted, keyring access failure, DB corruption) |

**Security rule:** AWS credentials (access key, secret key, session tokens) are **never** logged, even at DEBUG. The `S3Client` wrapper redacts these from all log output. S3 bucket names and object keys are safe to log.

**User access:** Help > "Show Log File" opens the log directory in the system file manager. When a user reports a bug, they can attach the log file.

---

## Core Features — Detailed

### 1. First-Run Experience

#### Detection

On launch, the app checks for an existing credential profile in the OS keyring (service name: `s3ui`, key: `profiles`). If none found, the setup wizard opens instead of the main window.

#### Wizard Flow

**Page 1: Welcome**

```
┌──────────────────────────────────────────┐
│                                          │
│          Welcome to S3UI                 │
│                                          │
│  S3UI lets you manage files on Amazon    │
│  S3 like a regular folder on your        │
│  computer.                               │
│                                          │
│  You'll need your AWS Access Key and     │
│  Secret Key to get started. These are    │
│  available in your AWS account under     │
│  IAM > Users > Security Credentials.     │
│                                          │
│                            [Get Started] │
└──────────────────────────────────────────┘
```

No "Skip" button. Credentials are required to do anything.

**Page 2: Credentials**

```
┌──────────────────────────────────────────┐
│  Connect to AWS                          │
│                                          │
│  Access Key ID                           │
│  ┌────────────────────────────────────┐  │
│  │ AKIA...                            │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Secret Access Key                       │
│  ┌────────────────────────────────────┐  │
│  │ ••••••••••••••••••••••••••••••••   │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Region                                  │
│  ┌────────────────────────────────┐      │
│  │ US East (N. Virginia)        ▼ │      │
│  └────────────────────────────────┘      │
│                                          │
│  [Test Connection]     ✓ Connected       │
│                                          │
│                     [Back]  [Continue]    │
└──────────────────────────────────────────┘
```

- The region dropdown uses human-readable names ("US East (N. Virginia)") mapped to region codes (`us-east-1`) internally.
- "Test Connection" calls `s3.list_buckets()`. On success, shows a green checkmark and enables "Continue." On failure, shows an inline error message in plain language below the button.
- The secret key field uses `QLineEdit.Password` echo mode. A toggle eye icon reveals/hides it.
- "Continue" is disabled until "Test Connection" succeeds.

**Page 3: Pick a Bucket**

```
┌──────────────────────────────────────────┐
│  Choose a Bucket                         │
│                                          │
│  Select the bucket you'd like to         │
│  manage. You can add more later.         │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │ ○  my-videos                      │  │
│  │ ●  my-website-assets              │  │
│  │ ○  backup-archive                 │  │
│  └────────────────────────────────────┘  │
│                                          │
│                     [Back]    [Finish]    │
└──────────────────────────────────────────┘
```

- Lists all buckets returned by `list_buckets()`.
- Single-select radio list. The first bucket is pre-selected.
- "Finish" stores the credential profile in keyring, saves the selected bucket in SQLite preferences, and opens the main window navigated to that bucket's root.

#### Post-Setup

After the wizard completes, subsequent launches skip straight to the main window. The wizard is also accessible from Settings > "Add Another Account" for additional profiles.

---

### 2. Credential Management

#### Keyring Schema

All credentials are stored under the keyring service name `s3ui`. Each profile is a separate keyring entry.

| Keyring Entry | Key | Value (JSON) |
|---|---|---|
| Profile "default" | `s3ui:profile:default` | `{"access_key_id": "AKIA...", "secret_access_key": "wJal...", "region": "us-east-1", "endpoint_url": null}` |
| Profile "work" | `s3ui:profile:work` | `{"access_key_id": "AKIA...", "secret_access_key": "xKq2...", "region": "eu-west-1", "endpoint_url": null}` |
| Profile index | `s3ui:profiles` | `["default", "work"]` |

The profile index is a JSON array of profile names, stored as a separate keyring entry so the app can enumerate profiles without knowing their names in advance.

#### CredentialStore API

```python
class CredentialStore:
    def list_profiles(self) -> list[str]
    def get_profile(self, name: str) -> Profile | None
    def save_profile(self, name: str, profile: Profile) -> None
    def delete_profile(self, name: str) -> None
    def test_connection(self, profile: Profile) -> TestResult

@dataclass
class Profile:
    access_key_id: str
    secret_access_key: str
    region: str
    endpoint_url: str | None = None

@dataclass
class TestResult:
    success: bool
    buckets: list[str]      # populated on success
    error_message: str       # plain language, populated on failure
    error_detail: str        # raw exception, populated on failure
```

#### Settings Dialog — Credentials Tab

```
┌──────────────────────────────────────────────────────┐
│  Settings                                            │
│                                                      │
│  [Credentials]  [Transfers]  [Cost Rates]  [General] │
│  ─────────────────────────────────────────────────── │
│                                                      │
│  Profiles                                            │
│  ┌──────────────────────────────────┐                │
│  │ ● default         [Edit] [Del]  │                │
│  │ ○ work            [Edit] [Del]  │                │
│  └──────────────────────────────────┘                │
│  [+ Add Profile]                                     │
│                                                      │
│  Active profile: default                             │
│                                                      │
│                                [Cancel]    [Save]    │
└──────────────────────────────────────────────────────┘
```

- Editing a profile opens the same credential form from the wizard.
- Deleting a profile requires confirmation: "Delete profile 'work'? Your credentials will be removed from the system keychain."
- The "active profile" determines which credentials are used for all S3 operations. Switching profiles re-initializes the S3 client and refreshes the bucket list.
- The bucket selector in the main toolbar shows buckets for the active profile only.

---

### 3. Dual-Pane File Browser

#### Main Window Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  S3UI                                                   [—][□][✕]│
├──────────────────────────────────────────────────────────────────┤
│  Menu: File  Edit  View  Go  Bucket  Help                        │
├──────────────────────────────────────────────────────────────────┤
│  [⚙]  [Profile: default ▼]                [Bucket: my-videos ▼] │
├──────────────────────────┬───────────────────────────────────────┤
│  Local Files             │  S3: my-videos                        │
│  [◀] [▶]  [🔍]          │  [◀] [▶]  [🔍]                       │
│  ~/Videos/uploads        │  / videos / 2025 / january /          │
├──────────────────────────┼───────────────────────────────────────┤
│  Name          Size Date │  Name              Size     Date      │
│  ──────────────────────  │  ────────────────────────────────     │
│  📁 raw/             ... │  📁 drafts/                   Jan 15  │
│  📁 exports/         ... │  📁 finals/                   Jan 22  │
│  🎬 clip-001.mp4 2.1 GB │  🎬 wedding.mp4    4.7 GB    Jan 20  │
│  🎬 clip-002.mp4 1.8 GB │  🎬 reception.mp4  3.2 GB    Jan 20  │
│  📄 notes.txt     12 KB │  📄 index.html      8 KB     Jan 18  │
│                          │  📄 style.css       2 KB     Jan 18  │
│                          │                                       │
│  5 items, 3.9 GB         │  6 items, 7.9 GB                     │
├──────────────────────────┴───────────────────────────────────────┤
│  Transfers (2 active, 1 queued)                        [⏸ All]  │
│  ↑ clip-001.mp4    ████████████░░░  78%  12.4 MB/s    ~47 sec  │
│  ↓ archive.zip     ██░░░░░░░░░░░░░  14%   8.1 MB/s   ~6 min   │
│  ⏸ clip-002.mp4    ░░░░░░░░░░░░░░░  Queued                     │
├──────────────────────────────────────────────────────────────────┤
│  Ready  │  47 objects  │  28.3 GB total  │  Est. $0.65/mo  │ ▶  │
└──────────────────────────────────────────────────────────────────┘
```

#### Widget Hierarchy

```
QMainWindow
├── QMenuBar
│   ├── File: New Window, Close, Quit
│   ├── Edit: Copy, Paste, Delete, Select All, Find
│   ├── View: Toggle Transfer Panel, Toggle Status Bar, Column Options
│   ├── Go: Back, Forward, Enclosing Folder, Go to Path...
│   ├── Bucket: Refresh, Stats, Cost Dashboard, Switch Bucket
│   └── Help: About, Open Documentation
│
├── QToolBar (top)
│   ├── QToolButton (Settings gear icon)
│   ├── QComboBox (Profile selector) — only shown if >1 profile
│   ├── QWidget (spacer)
│   └── QComboBox (Bucket selector)
│
├── QSplitter (horizontal, central widget)
│   ├── LocalPaneWidget
│   │   ├── QToolBar (mini, embedded)
│   │   │   ├── QToolButton (Back)
│   │   │   ├── QToolButton (Forward)
│   │   │   ├── QToolButton (Search/Filter toggle)
│   │   │   └── BreadcrumbBar (custom QWidget — clickable path segments)
│   │   ├── QLineEdit (filter bar, hidden until search toggled)
│   │   ├── QTreeView (file listing)
│   │   │   └── Model: QFileSystemModel (filtered to show files + dirs)
│   │   └── QLabel (status: "5 items, 3.9 GB")
│   │
│   └── S3PaneWidget
│       ├── QToolBar (mini, embedded)
│       │   ├── QToolButton (Back)
│       │   ├── QToolButton (Forward)
│       │   ├── QToolButton (Search/Filter toggle)
│       │   └── BreadcrumbBar (clickable path segments)
│       ├── QLineEdit (filter bar, hidden until search toggled)
│       ├── QTableView (object listing)
│       │   └── Model: S3ObjectModel (custom QAbstractTableModel)
│       └── QLabel (status: "6 items, 7.9 GB")
│
├── QDockWidget (bottom, Transfer Panel)
│   └── TransferPanelWidget
│       ├── QToolBar (mini)
│       │   ├── QLabel ("Transfers (2 active, 1 queued)")
│       │   ├── QWidget (spacer)
│       │   └── QToolButton (Pause All / Resume All)
│       └── QTableView (transfer listing)
│           └── Model: TransferModel (custom QAbstractTableModel)
│
└── QStatusBar
    ├── QLabel ("Ready" / "Uploading..." / "Scanning bucket...")
    ├── QLabel ("47 objects")
    ├── QLabel ("28.3 GB total")
    ├── QLabel ("Est. $0.65/mo") — clickable, opens Cost Dashboard
    └── QProgressBar (hidden unless a bucket scan is running)
```

#### Left Pane: Local Files — Detailed

**Model:** `QFileSystemModel` — Qt's built-in filesystem model. Provides:
- Lazy directory loading (no full disk scan)
- Native file icons from the OS
- File watching (auto-updates when files change on disk)
- Sorting by name, size, date

**Root path:** Stored in preferences. Defaults to the user's home directory. Changeable via:
- Breadcrumb bar: click any segment to jump up
- Right-click > "Set as Root Folder"
- Go > "Choose Folder..." (native directory picker)

**Columns displayed:**

| Column | Source | Notes |
|---|---|---|
| Name | `QFileSystemModel.fileName` | With system file icon |
| Size | `QFileSystemModel.size` | Formatted: bytes→KB→MB→GB. Blank for directories. |
| Date Modified | `QFileSystemModel.lastModified` | "2 hours ago" if < 24h, "Jan 28" if same year, "Jan 28, 2024" otherwise |

**Behavior:**
- Directories sort before files (like Finder/Explorer)
- Hidden files are hidden by default (toggle in View menu)
- Symlinks are followed
- The pane footer shows item count and total size of the current directory
- Double-click a directory: navigates into it
- Double-click a file: opens with system default app (`QDesktopServices.openUrl`)

**Navigation history:**
- Back/Forward buttons maintain a stack of visited paths (like browser history)
- Stack is capped at 50 entries
- Keyboard: Alt+Left (Back), Alt+Right (Forward) on all platforms; Cmd+[ / Cmd+] also on macOS

#### Right Pane: S3 Browser — Detailed

**Model:** `S3ObjectModel` — custom `QAbstractTableModel` subclass.

The model represents a single "directory listing" — all common prefixes and objects at a given prefix level. It does not hold the full bucket tree in memory.

**Data structure per listing:**

```python
@dataclass
class S3Item:
    name: str               # Display name: "photo.jpg" or "folder/"
    key: str                # Full S3 key: "videos/2025/january/photo.jpg"
    is_prefix: bool         # True for "folders" (common prefixes)
    size: int | None        # Bytes, None for prefixes
    last_modified: datetime | None  # None for prefixes
    storage_class: str | None       # "STANDARD", "INTELLIGENT_TIERING", etc.
    etag: str | None
```

**Fetching:**

When the user navigates to a prefix:

1. `S3ObjectModel` emits `loading_started` signal (S3Pane shows a spinner)
2. A `QThread` calls `s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter='/')` with pagination
3. Common prefixes become folder-type `S3Item`s
4. Objects become file-type `S3Item`s
5. Results are emitted via `listing_ready(prefix, list[S3Item])` signal
6. Model calls `beginResetModel()`, replaces internal data, calls `endResetModel()`
7. S3Pane hides the spinner

**Pagination:** For prefixes with >1000 items, pagination tokens are followed automatically within the worker thread. The model receives the complete listing when all pages are fetched. A progress signal updates the status footer during long listings ("Loading... 3,000 objects").

**Caching:** See the dedicated **Cache & Sync Strategy** section for full details. In short: cached listings are shown instantly on revisit, background revalidation refreshes stale data without disrupting the UI, and the app's own operations update the model and cache optimistically with zero API calls.

**Columns displayed (default):**

| Column | Source | Notes |
|---|---|---|
| Name | `S3Item.name` | With folder/file icon. Folder icon for prefixes, file icon from extension via `QFileIconProvider`. |
| Size | `S3Item.size` | Formatted same as local pane. "—" for folders. |
| Date Modified | `S3Item.last_modified` | Same relative/absolute formatting as local pane. "—" for folders. |

**Optional columns** (toggle in View > Columns):
| Column | Source |
|---|---|
| Storage Class | `S3Item.storage_class` |
| Full Key | `S3Item.key` |

**Behavior:**
- Prefixes (folders) sort before objects (files), same as local pane
- Double-click a prefix: navigates into it
- Double-click a file: downloads to a temp directory and opens with system default app
- Right-click context menu (see Interactions section)
- The pane footer shows item count and total size of the current prefix listing

**Navigation:**
- Breadcrumb bar: each path segment is a clickable button. Clicking "videos" when the path is `/ videos / 2025 / january /` navigates to the `videos/` prefix.
- Back/Forward buttons maintain a history stack of visited prefixes.
- The breadcrumb bar is also an editable text field — clicking the path text area (not a segment button) reveals a text input where the user can type or paste a prefix path directly. Press Enter to navigate, Escape to cancel. Same behavior as Finder's path bar or Chrome's address bar.

**Drag and drop — S3 pane as target (upload):**

1. User drags files/folders from LocalPane (or from the OS file manager via external drag)
2. The S3 pane shows a drop highlight on the current prefix (blue border)
3. On drop:
   - For each file: create a transfer record with `object_key = current_prefix + relative_path`
   - For directories: walk recursively, create one transfer record per file preserving structure
   - All transfers are enqueued in the TransferEngine

**Drag and drop — S3 pane as source (download):**

1. User drags files/folders from S3Pane to LocalPane
2. The local pane shows a drop highlight
3. On drop:
   - For each object: create a download transfer record with `local_path = local_pane_current_dir + object_name`
   - For prefixes: recursively list all objects under the prefix, create one transfer per object preserving structure
   - Name conflicts: prompt "Replace, Skip, or Rename" (same as OS file managers)

**Drag and drop — within S3 pane (move):**

1. User drags objects/prefixes onto a different prefix within the S3 pane
2. Drop target prefix highlights
3. On drop: move operation (server-side copy + delete). See File Operations > Move.

#### Breadcrumb Bar — Detailed

Custom widget: a horizontal layout of clickable `QToolButton`s with separators.

```
[ / ] [ ▸ ] [ videos ] [ ▸ ] [ 2025 ] [ ▸ ] [ january ]
```

- Each segment is a `QToolButton` with flat style (no border, text only)
- Separators are `QLabel("▸")` with dimmed color
- Clicking a segment navigates to that prefix level
- The root segment `/` navigates to the bucket root
- If the path is too long for the available width, leading segments collapse into a `...` dropdown menu
- Clicking the whitespace area to the right of the last segment enters edit mode: the breadcrumb buttons hide and a `QLineEdit` appears with the full path text, pre-selected. Type a new path and press Enter to navigate. Press Escape or click away to cancel.

#### Context Menus

**Right-click on file(s) in S3 pane:**

```
┌─────────────────────────┐
│  Download               │  ← downloads to local pane's current directory
│  Download to...         │  ← native directory picker
│  ───────────────────    │
│  Rename                 │  ← inline edit in the table
│  Move to...             │  ← prefix picker dialog
│  Copy to...             │  ← prefix/bucket picker dialog
│  ───────────────────    │
│  Delete                 │  ← confirmation dialog
│  ───────────────────    │
│  Get Info               │  ← details dialog: size, class, key, etag, modified
└─────────────────────────┘
```

**Right-click on folder (prefix) in S3 pane:**

```
┌─────────────────────────┐
│  Open                   │  ← navigate into it
│  Download Folder        │  ← recursive download
│  Download Folder to...  │
│  ───────────────────    │
│  Rename                 │  ← renames all objects under this prefix
│  Delete Folder          │  ← recursive delete with confirmation
│  ───────────────────    │
│  Get Info               │  ← total size, object count under prefix
└─────────────────────────┘
```

**Right-click on file(s) in Local pane:**

```
┌─────────────────────────┐
│  Open                   │  ← system default app
│  Upload to S3           │  ← uploads to S3 pane's current prefix
│  Upload to...           │  ← prefix picker dialog
│  ───────────────────    │
│  Show in Finder/Explorer│  ← reveals in OS file manager
└─────────────────────────┘
```

**Right-click on empty space in S3 pane:**

```
┌─────────────────────────┐
│  New Folder             │  ← creates a zero-byte object with trailing /
│  Upload Files...        │  ← native file picker
│  Upload Folder...       │  ← native directory picker
│  ───────────────────    │
│  Refresh                │  ← re-fetches current listing
│  ───────────────────    │
│  Paste                  │  ← if objects were copied with Cmd/Ctrl+C
└─────────────────────────┘
```

#### Keyboard Shortcuts

| Action | macOS | Windows/Linux |
|---|---|---|
| Copy (for paste within S3) | Cmd+C | Ctrl+C |
| Paste | Cmd+V | Ctrl+V |
| Delete | Cmd+Backspace | Delete |
| Select All | Cmd+A | Ctrl+A |
| Find/Filter | Cmd+F | Ctrl+F |
| Rename | Enter (with item selected) | F2 |
| Navigate Back | Cmd+[ or Alt+Left | Alt+Left |
| Navigate Forward | Cmd+] or Alt+Right | Alt+Right |
| Go to Parent Folder | Cmd+Up | Alt+Up |
| Refresh | Cmd+R | F5 |
| New Folder | Cmd+Shift+N | Ctrl+Shift+N |
| Open Settings | Cmd+, | Ctrl+, |
| Toggle Transfer Panel | Cmd+T | Ctrl+T |
| Focus Local Pane | Cmd+1 | Ctrl+1 |
| Focus S3 Pane | Cmd+2 | Ctrl+2 |

#### Search / Filter

Each pane has a filter bar toggled by Cmd/Ctrl+F or the search icon.

**Behavior:**
- Filters the current listing client-side by name substring (case-insensitive)
- Does not search recursively into sub-prefixes (that would require listing the entire bucket)
- As-you-type: listing updates on every keystroke
- Escape or clicking the X closes the filter bar and restores the full listing
- The footer updates to show filtered count: "3 of 47 items"

---

### 4. File Operations — Detailed

#### Upload: Multipart Flow

Files under 8 MB use a single `put_object` call. Files 8 MB and over use multipart upload.

**Multipart upload state machine:**

```
                    ┌──────────┐
                    │  QUEUED  │
                    └────┬─────┘
                         │ TransferEngine picks up
                         ▼
                   ┌───────────┐
              ┌───►│ INITIATING│ call create_multipart_upload()
              │    └─────┬─────┘
              │          │ success → store upload_id in SQLite
              │          ▼
              │    ┌───────────┐
              │    │ UPLOADING │ upload parts in sequence
              │    │ PARTS     │ (within this worker, parts are sequential;
              │    │           │  concurrency is across different files)
              │    └──┬──┬──┬──┘
              │       │  │  │
              │       │  │  └──── part failed → RETRY_PART (up to 3x)
              │       │  └─────── user paused → PAUSED (store last completed part#)
              │       └────────── all parts done
              │          │
              │          ▼
              │    ┌────────────┐
              │    │ COMPLETING │ call complete_multipart_upload()
              │    └─────┬──────┘
              │          │ success
              │          ▼
              │    ┌────────────┐
              │    │ COMPLETED  │
              │    └────────────┘
              │
              │    On failure (after retries exhausted):
              │    ┌────────────┐
              └────│  FAILED    │ upload_id preserved for resume
                   └────────────┘
```

**Part tracking in SQLite:**

```sql
CREATE TABLE transfer_parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id     INTEGER NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
    part_number     INTEGER NOT NULL,
    offset          INTEGER NOT NULL,    -- byte offset in the source file
    size            INTEGER NOT NULL,    -- part size in bytes
    etag            TEXT,                -- set after successful upload
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','completed','failed')),
    UNIQUE(transfer_id, part_number)
);
```

**Resume logic:**

When resuming a transfer (either after pause or after app restart):

1. Read the `upload_id` and `transfer_id` from the `transfers` table
2. Query `transfer_parts` for parts where `status != 'completed'`
3. Call `list_parts()` on S3 to verify which parts S3 has received (in case the app crashed after uploading a part but before updating SQLite)
4. Reconcile: mark any parts that S3 confirms as `completed` in SQLite
5. Upload remaining parts starting from the first incomplete one
6. Call `complete_multipart_upload()` with the full parts list

**Part size selection:**

| File Size | Part Size | Max Parts | Why |
|---|---|---|---|
| 8 MB – 50 GB | 8 MB | 6,250 | Good balance of resume granularity and overhead |
| 50 GB – 500 GB | 64 MB | 7,813 | Fewer parts, less overhead for very large files |
| 500 GB – 5 TB | 512 MB | 9,766 | S3 max is 10,000 parts; stay under limit |

The app auto-selects part size based on file size. The user never sees this.

#### Download: Ranged GET Flow

Files under 8 MB use a single `get_object` call. Files 8 MB and over use ranged GETs.

1. `head_object` to get total size and ETag
2. Divide into 8 MB ranges
3. Download each range sequentially within a single worker, writing to a temp file
4. On completion, rename temp file to final destination (atomic on most filesystems)
5. Verify total bytes match expected size

**Resume logic:**

If a download is interrupted, the temp file is preserved. On resume:

1. Check temp file size on disk
2. Resume from byte offset = temp file size
3. Use `Range: bytes=<offset>-` header

No part tracking table needed for downloads — the temp file itself is the state.

**Temp file naming:** `<destination_dir>/.s3ui-download-<transfer_id>.tmp`

The `.s3ui-download-` prefix ensures temp files are recognizable and cleanable.

#### Move / Rename — Detailed

S3 has no native move or rename. Every move is copy + delete.

**Single file rename:**

1. User selects a file, presses Enter (macOS) or F2 (Win/Linux)
2. The name cell becomes an editable `QLineEdit` (inline editing via `QStyledItemDelegate`)
3. On commit (Enter):
   - Validate new name (no `/` characters, not empty, not same as old)
   - Call `copy_object(source, destination, MetadataDirective='COPY')` — server-side, no data transfer. `MetadataDirective='COPY'` preserves the original object's Content-Type, Cache-Control, and all custom metadata. Without this flag, S3 defaults to `REPLACE` and strips metadata.
   - On copy success, call `delete_object(source)`
   - Refresh the listing
4. On cancel (Escape): revert to original name

**Folder rename:**

Renaming a prefix means copying and deleting every object under it.

1. List all objects under the old prefix (paginated)
2. Show a progress dialog: "Renaming folder... (142 of 380 files)"
3. For each object: `copy_object` with new prefix, then `delete_object`
4. Uses batch delete (`delete_objects`, up to 1000 keys per call) for efficiency after all copies succeed
5. If any copy fails, the operation stops and shows which files couldn't be renamed. Already-copied files exist at both old and new paths; the user is informed and can retry.

**Cross-prefix move (drag within S3 pane):**

Same as rename but with a different target prefix. The operation is:
- `copy_object(source_bucket, source_key, dest_bucket, dest_prefix + source_name, MetadataDirective='COPY')`
- `delete_object(source_bucket, source_key)`

All copy operations (rename, move, copy within S3) must use `MetadataDirective='COPY'` to preserve the original object's metadata. This is handled inside `S3Client.copy_object()` so callers don't need to remember it.

**Cross-bucket move:**

Same flow but `copy_object` specifies a different destination bucket. Cross-region copies are supported by boto3 but may be slow (data transfers through the client). A warning is shown: "Moving between regions will download and re-upload the file."

#### Delete — Detailed

**Single file / multi-select delete:**

1. User presses Delete or right-click > Delete
2. Confirmation dialog:

```
┌────────────────────────────────────────────────┐
│  Delete 3 files?                               │
│                                                │
│  • wedding-raw.mp4 (4.7 GB)                   │
│  • reception.mp4 (3.2 GB)                      │
│  • thumbnail.jpg (1.2 MB)                      │
│                                                │
│  Total: 7.9 GB                                 │
│                                                │
│  This cannot be undone. Deleted files cannot    │
│  be recovered unless bucket versioning is       │
│  enabled.                                       │
│                                                │
│                        [Cancel]     [Delete]    │
└────────────────────────────────────────────────┘
```

3. If more than 10 files, the dialog shows the first 10 with "...and 35 more files" below
4. Uses `delete_objects` API (batch delete, up to 1000 keys per request)
5. If batch delete returns partial failures, the UI reports which files couldn't be deleted

**Folder delete:**

1. Right-click > Delete Folder on a prefix
2. App lists all objects under the prefix (in a background thread) to determine count and total size
3. Confirmation dialog: "Delete folder 'january/' and all 142 files inside (28.3 GB)? This cannot be undone."
4. Batch delete all objects using `delete_objects` with pagination
5. Progress shown: "Deleting... 142 of 142 files"

#### New Folder

S3 has no native directory concept. Creating a "folder" means creating a zero-byte object with a trailing `/` in the key.

1. Right-click > New Folder (or Cmd/Ctrl+Shift+N)
2. A new row appears in the S3 listing with an editable name field, pre-filled with "New Folder"
3. User types a name and presses Enter
4. App calls `put_object(Key=current_prefix + name + '/', Body=b'')`
5. The listing refreshes, showing the new folder

#### Operation Race Conditions

S3 operations are asynchronous and the user can trigger new operations before previous ones complete. Without protection, conflicts arise — e.g., deleting a folder while an upload into it is in progress, or renaming a file that's being moved.

**Simple locking strategy:**

The `S3PaneWidget` maintains an `_operation_locks: dict[str, str]` mapping S3 key prefixes to operation descriptions. Before starting a destructive operation (delete, rename, move) on a key or prefix:

1. Check if any key in the operation's scope is locked
2. If locked: show a non-modal warning: "Cannot delete 'videos/' — a transfer is in progress. Wait for it to finish or cancel it first."
3. If unlocked: acquire locks for all affected keys, proceed, release on completion (success or failure)

| Operation | Locks Acquired |
|---|---|
| Upload to `videos/clip.mp4` | `videos/clip.mp4` |
| Delete `videos/` (recursive) | `videos/` (prefix lock — blocks any operation on keys starting with `videos/`) |
| Rename `clip.mp4` → `clip-final.mp4` | `videos/clip.mp4` + `videos/clip-final.mp4` |
| Move files into `archive/` | source keys + destination keys |

Prefix locks block operations on any key that starts with the locked prefix. This is checked with a simple `startswith()` comparison.

The lock dict is UI-only (main thread, no threading concerns). It doesn't need to be persistent — if the app restarts, there are no in-flight operations to conflict with (transfers are re-queued cleanly by `restore_pending()`).

#### Copy within S3

1. Select files in S3 pane, Cmd/Ctrl+C
2. App stores the selected keys in an internal clipboard (not the OS clipboard — S3 keys aren't useful on the OS clipboard)
3. Navigate to the destination prefix
4. Cmd/Ctrl+V or right-click > Paste
5. For each key in the clipboard: `copy_object` (server-side)
6. Name conflicts: append " (copy)" suffix, or prompt if the file already exists

---

### 5. Transfer Management — Detailed

#### TransferEngine Architecture

```python
class TransferEngine(QObject):
    """Manages the transfer queue and worker pool."""

    transfer_progress = pyqtSignal(int, int, int)  # transfer_id, bytes_done, total
    transfer_speed = pyqtSignal(int, float)          # transfer_id, bytes_per_sec
    transfer_status_changed = pyqtSignal(int, str)   # transfer_id, new_status
    transfer_error = pyqtSignal(int, str, str)        # transfer_id, user_msg, detail

    def __init__(self, s3_client, db, max_workers=4):
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max_workers)
        self._active: dict[int, TransferWorker] = {}   # transfer_id → worker
        self._paused_global = False

    def enqueue(self, transfer_id: int) -> None
    def pause(self, transfer_id: int) -> None
    def resume(self, transfer_id: int) -> None
    def cancel(self, transfer_id: int) -> None
    def pause_all(self) -> None
    def resume_all(self) -> None
    def retry(self, transfer_id: int) -> None
    def restore_pending(self) -> None  # called on app start
```

**Queue prioritization:**

Transfers are processed in FIFO order. When a slot opens (a transfer completes, fails, or is paused), the engine picks the next `queued` transfer from SQLite ordered by `created_at ASC`.

**Speed calculation:**

Each worker tracks bytes transferred in a rolling 3-second window. Speed is emitted every 500ms as a signal. The ETA is calculated as `(total_bytes - bytes_done) / current_speed`. Displayed as:

| ETA | Display |
|---|---|
| < 5 seconds | "A few seconds" |
| 5–60 seconds | "~30 sec" (rounded to nearest 5) |
| 1–60 minutes | "~4 min" (rounded to nearest minute) |
| 1–24 hours | "~2 hr 15 min" |
| > 24 hours | "~1 day 3 hr" |

**Pause behavior:**

1. Worker checks a `threading.Event` flag between each part upload/download
2. If paused: worker returns immediately, status set to `paused` in SQLite
3. Current part may complete (we don't interrupt mid-part to avoid wasted work)
4. Resume: a new worker is submitted with the same transfer_id; it picks up from the last completed part

**Cancel behavior:**

1. Sets the cancel event flag on the worker
2. Worker stops after current part
3. For uploads: calls `abort_multipart_upload()` to clean up incomplete parts on S3
4. Removes the transfer record from SQLite (or marks as `cancelled` for history)
5. No partial file left on S3

**Orphaned multipart upload cleanup:**

If the app crashes during a multipart upload, the abort never fires and S3 continues holding the incomplete parts (which incur storage charges). On startup, after `restore_pending()` processes known transfers, the app runs a cleanup pass:

1. Call `list_multipart_uploads()` on each active bucket
2. For each in-progress multipart upload on S3:
   - If the `upload_id` matches a known transfer in SQLite → leave it (it will be resumed)
   - If the `upload_id` is unknown (orphaned) and older than 24 hours → call `abort_multipart_upload()` to free the parts
   - If unknown but less than 24 hours old → leave it (might belong to another tool or a just-started upload)
3. Log each abort at INFO level

This runs once on startup in a low-priority background thread. The 24-hour grace period avoids aborting uploads from concurrent tools. For buckets with S3 lifecycle rules that auto-abort incomplete uploads, this is redundant but harmless.

**Retry behavior:**

Each part upload/download is retried up to 3 times with exponential backoff plus jitter:
- Attempt 1: immediate
- Attempt 2: 1s + random(0–500ms) delay
- Attempt 3: 4s + random(0–2s) delay

Jitter (uniformly distributed random component) prevents multiple failed transfers from retrying in lockstep — a thundering herd scenario where all workers hammer S3 at the same instant after a transient outage.

If all 3 attempts fail, the transfer status is set to `failed` with an error message.

The user can manually retry a failed transfer. This resets the attempt counter and re-enqueues the transfer.

**App restart persistence:**

On startup, `TransferEngine.restore_pending()`:

1. Queries SQLite for all transfers with `status IN ('queued', 'in_progress', 'paused')`
2. Sets any `in_progress` transfers to `queued` (they were interrupted by the app closing)
3. Enqueues them all in order
4. For uploads: validates that the local source file still exists. If not, marks as `failed` with message "Source file no longer exists."
5. For downloads: validates that the destination directory still exists. If not, marks as `failed`.

**System notifications:**

When a transfer completes and the app is not the foreground window:

- macOS: `NSUserNotification` via `QSystemTrayIcon.showMessage()`
- Windows: Toast notification
- Linux: `org.freedesktop.Notifications` via Qt

Notification text: "Upload complete: wedding-raw.mp4 (4.7 GB)"

Clicking the notification brings the app to the foreground.

#### Transfer Panel — Detailed UI

```
┌──────────────────────────────────────────────────────────────────┐
│  Transfers (2 active, 1 queued, 5 completed)            [⏸ All] │
├──────┬──────────────────┬──────────────┬────────┬───────┬───────┤
│  Dir │  File            │  Progress    │  Speed │ ETA   │       │
├──────┼──────────────────┼──────────────┼────────┼───────┼───────┤
│  ↑   │  clip-001.mp4    │ ████████░░ 78% │ 12 MB/s│ ~47s │ [⏸][✕]│
│  ↓   │  archive.zip     │ ██░░░░░░░░ 14% │  8 MB/s│ ~6m  │ [⏸][✕]│
│  ↑   │  clip-002.mp4    │ ░░░░░░░░░░  Q │   —    │  —   │    [✕]│
│  ↑   │  notes.txt       │ ██████████  ✓ │   —    │  —   │      │
│  ↓   │  intro.mp4       │ ██████████  ✓ │   —    │  —   │      │
├──────┴──────────────────┴──────────────┴────────┴───────┴───────┤
│  ⚠ Failed: style.css — Access denied. [Retry]                   │
└──────────────────────────────────────────────────────────────────┘
```

- Active transfers show pause button [⏸] and cancel button [✕]
- Queued transfers show cancel button only
- Completed transfers show a checkmark, no buttons. Auto-clear after session.
- Failed transfers are pinned to the bottom with the error message and a [Retry] button.
- The panel header shows counts: "2 active, 1 queued, 5 completed"
- The panel is collapsible. When collapsed, the header is still visible as a thin bar.

---

### 6. Bucket Statistics — Detailed

#### Scanning Process

A bucket scan lists every object to compute aggregate statistics. This can be slow for large buckets.

**Implementation:**

1. User clicks "Bucket Stats" in the toolbar or Bucket menu
2. The stats dialog opens. If no recent snapshot exists (or the last one is >24 hours old), a scan starts automatically.
3. Scan runs in a dedicated `QThread`:
   - Calls `list_objects_v2` with pagination (1000 objects per page)
   - For each page: accumulates counts and sizes by storage class, tracks the top-10 largest objects
   - Emits `scan_progress(objects_counted)` every page
4. On completion: writes a `bucket_snapshots` row and emits `scan_complete(snapshot)`
5. The dialog updates with the new data

**Progress UI in the stats dialog:**

```
┌──────────────────────────────────────────────────────┐
│  Bucket Stats: my-videos                      [✕]   │
│                                                      │
│  Scanning... 14,328 objects counted                  │
│  [████████████░░░░░░░░░░░░░░░░░░]                   │
│  (Progress bar is indeterminate — we don't know      │
│   total objects until the scan completes)             │
│                                                      │
│  [Cancel Scan]                                       │
└──────────────────────────────────────────────────────┘
```

After completion:

```
┌──────────────────────────────────────────────────────┐
│  Bucket Stats: my-videos               [Refresh] [✕] │
│                                                      │
│  Last scanned: 2 hours ago                           │
│                                                      │
│  Total: 14,328 objects — 284.3 GB                    │
│                                                      │
│  Storage Breakdown                                   │
│  ┌────────────────────────────────────────────────┐  │
│  │ ████████████████████████████████░░░░ Standard  │  │
│  │ ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░ Std-IA   │  │
│  │ ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Glacier   │  │
│  └────────────────────────────────────────────────┘  │
│  Standard:   246.1 GB  (12,841 objects)              │
│  Std-IA:      32.8 GB  (1,204 objects)               │
│  Glacier:      5.4 GB  (283 objects)                 │
│                                                      │
│  Largest Files                                       │
│  1. wedding-4k-raw.mp4           12.8 GB   Standard  │
│  2. drone-footage-2024.mp4        8.4 GB   Standard  │
│  3. full-backup-jan.tar.gz        6.2 GB   Std-IA   │
│  ...                                                 │
│                                                      │
│  Storage Over Time                                   │
│  300 GB │              ___________                   │
│  200 GB │         ____/                              │
│  100 GB │    ____/                                   │
│     0   │___/                                        │
│         └──────────────────────────────               │
│          Oct   Nov   Dec   Jan   Feb                 │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**Charts:** Rendered with `pyqtgraph` (preferred — lightweight, no matplotlib overhead) or `matplotlib` as fallback. The storage-over-time chart uses data from the `bucket_snapshots` table, plotting one point per day.

---

### 7. Cost Estimation — Detailed

#### How Costs Are Tracked

Cost estimation combines two data sources:

1. **Storage costs:** From bucket snapshots. Each daily snapshot records total bytes by storage class. Storage cost = `(bytes / 1024³) * rate_per_gb_month / 30` per day.

2. **Operational costs:** From instrumented S3 client calls. Every S3 API call made by the app is intercepted and counted.

**S3 client instrumentation:**

The `S3Client` wrapper counts every API call by type:

```python
class S3Client:
    """Wrapper around boto3 S3 client that counts API calls."""

    def list_objects_v2(self, **kwargs):
        self._cost_tracker.record_request('list')
        return self._client.list_objects_v2(**kwargs)

    def put_object(self, **kwargs):
        self._cost_tracker.record_request('put')
        size = len(kwargs.get('Body', b''))
        self._cost_tracker.record_upload_bytes(size)
        return self._client.put_object(**kwargs)

    def upload_part(self, **kwargs):
        self._cost_tracker.record_request('put')
        # bytes tracked by the transfer engine per-part
        return self._client.upload_part(**kwargs)

    def get_object(self, **kwargs):
        self._cost_tracker.record_request('get')
        return self._client.get_object(**kwargs)

    def delete_object(self, **kwargs):
        self._cost_tracker.record_request('delete')
        return self._client.delete_object(**kwargs)

    def delete_objects(self, **kwargs):
        count = len(kwargs['Delete']['Objects'])
        self._cost_tracker.record_request('delete', count=count)
        return self._client.delete_objects(**kwargs)

    def copy_object(self, **kwargs):
        self._cost_tracker.record_request('put')  # copy counts as PUT
        return self._client.copy_object(**kwargs)
```

**CostTracker writes to SQLite:**

```python
class CostTracker:
    def record_request(self, request_type: str, count: int = 1):
        """Increment today's request count for the active bucket."""
        today = date.today().isoformat()
        # UPSERT into daily_usage
        self._db.execute("""
            INSERT INTO daily_usage (bucket_id, usage_date, {col})
            VALUES (?, ?, ?)
            ON CONFLICT(bucket_id, usage_date)
            DO UPDATE SET {col} = {col} + ?
        """.format(col=f'{request_type}_requests'), ...)

    def record_upload_bytes(self, size: int): ...
    def record_download_bytes(self, size: int): ...
```

#### Cost Calculation Formulas

```python
def calculate_daily_cost(snapshot, usage, rates) -> DailyCost:
    # Storage cost (prorated daily from monthly rate)
    storage = (
        (snapshot.standard_bytes / GB) * rates['standard_storage'] / 30 +
        (snapshot.ia_bytes / GB) * rates['ia_storage'] / 30 +
        (snapshot.glacier_bytes / GB) * rates['glacier_storage'] / 30
    )

    # Request cost
    requests = (
        (usage.put_requests / 1000) * rates['put_request'] +
        (usage.get_requests / 1000) * rates['get_request'] +
        (usage.list_requests / 1000) * rates['list_request'] +
        (usage.delete_requests / 1000) * rates['delete_request']
    )

    # Data transfer cost (tiered)
    transfer_gb = usage.bytes_downloaded / GB
    if transfer_gb <= 100:
        transfer = transfer_gb * rates['transfer_out_first_100gb']
    else:
        transfer = (
            100 * rates['transfer_out_first_100gb'] +
            (transfer_gb - 100) * rates['transfer_out_next_9.9tb']
        )
    # Upload (transfer in) is free

    return DailyCost(storage=storage, requests=requests, transfer=transfer)
```

#### Default Rate Table

Stored in the `cost_rates` SQLite table. Initialized on first run with these defaults:

| Name | Rate | Unit |
|---|---|---|
| `standard_storage` | 0.023 | $/GB-month |
| `ia_storage` | 0.0125 | $/GB-month |
| `glacier_storage` | 0.004 | $/GB-month |
| `glacier_deep_storage` | 0.00099 | $/GB-month |
| `intelligent_tiering_frequent` | 0.023 | $/GB-month |
| `intelligent_tiering_infrequent` | 0.0125 | $/GB-month |
| `put_request` | 0.005 | $/1,000 requests |
| `get_request` | 0.0004 | $/1,000 requests |
| `list_request` | 0.005 | $/1,000 requests |
| `delete_request` | 0.0 | $/1,000 requests (free) |
| `transfer_out_first_100gb` | 0.09 | $/GB |
| `transfer_out_next_9.9tb` | 0.085 | $/GB |

Editable in Settings > Cost Rates tab for users in different regions or using negotiated pricing.

#### Cost Dashboard

```
┌──────────────────────────────────────────────────────────────────┐
│  Cost Dashboard                                    [Export CSV] [✕]│
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Estimated Cost This Month: $4.82                                │
│  ───────────────────────────────────────                         │
│  Storage:   $3.91  (284.3 GB average)                            │
│  Requests:  $0.24  (48,200 total)                                │
│  Transfer:  $0.67  (7.4 GB downloaded)                           │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Daily Breakdown (last 30 days)                                  │
│                                                                  │
│  $0.25│  ░░                                                      │
│  $0.20│  ██░░                    ░░                               │
│  $0.15│  ████░░          ░░░░   ████                             │
│  $0.10│  ██████░░  ░░░░ ██████  ██████░░                         │
│  $0.05│  ████████  ████ ████████████████░░                       │
│  $0.00│  ████████  ████ ██████████████████                       │
│       └──────────────────────────────────                        │
│        Jan 1     Jan 8   Jan 15   Jan 22   Jan 29               │
│                                                                  │
│  Legend: ██ Storage  ░░ Requests  ▓▓ Transfer                    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Per Bucket                                                      │
│  ┌──────────────────┬──────────┬──────────┬──────────┬────────┐ │
│  │ Bucket           │ Storage  │ Requests │ Transfer │ Total  │ │
│  ├──────────────────┼──────────┼──────────┼──────────┼────────┤ │
│  │ my-videos        │ $3.42    │ $0.18    │ $0.52    │ $4.12  │ │
│  │ my-website       │ $0.49    │ $0.06    │ $0.15    │ $0.70  │ │
│  ├──────────────────┼──────────┼──────────┼──────────┼────────┤ │
│  │ Total            │ $3.91    │ $0.24    │ $0.67    │ $4.82  │ │
│  └──────────────────┴──────────┴──────────┴──────────┴────────┘ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**CSV Export format:**

```csv
date,bucket,storage_gb,storage_cost,put_requests,get_requests,list_requests,delete_requests,request_cost,bytes_downloaded,transfer_cost,total_cost
2025-01-28,my-videos,246.1,0.189,142,1840,28,0,0.001,524288000,0.044,0.234
2025-01-28,my-website,38.2,0.029,14,420,12,2,0.000,0,0.000,0.030
```

**Status bar cost display:**

The status bar shows: `Est. $4.82/mo`

This is the sum of:
- Today's storage cost × remaining days in month (prorated)
- Month-to-date request costs
- Month-to-date transfer costs

Updated every time the app records a new API call or when a new daily snapshot is computed.

---

## Data Model (SQLite) — Complete

Database location: `~/.s3ui/s3ui.db`

SQLite is opened in WAL (Write-Ahead Logging) mode for concurrent read/write from multiple threads without blocking.

### Tables

```sql
-- Tracks known buckets and their associated credential profile
CREATE TABLE buckets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    region      TEXT,
    profile     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, profile)  -- same bucket name under different profiles is allowed
);

-- Daily storage snapshots from bucket scans
CREATE TABLE bucket_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    snapshot_date   TEXT NOT NULL,
    total_objects   INTEGER,
    total_bytes     INTEGER,
    standard_bytes  INTEGER DEFAULT 0,
    ia_bytes        INTEGER DEFAULT 0,
    glacier_bytes   INTEGER DEFAULT 0,
    deep_archive_bytes  INTEGER DEFAULT 0,
    intelligent_tiering_bytes INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(bucket_id, snapshot_date)
);
CREATE INDEX idx_snapshots_bucket_date ON bucket_snapshots(bucket_id, snapshot_date);

-- Daily API usage and transfer volume
CREATE TABLE daily_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    usage_date      TEXT NOT NULL,
    bytes_uploaded  INTEGER DEFAULT 0,
    bytes_downloaded INTEGER DEFAULT 0,
    put_requests    INTEGER DEFAULT 0,
    get_requests    INTEGER DEFAULT 0,
    list_requests   INTEGER DEFAULT 0,
    delete_requests INTEGER DEFAULT 0,
    copy_requests   INTEGER DEFAULT 0,
    head_requests   INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(bucket_id, usage_date)
);
CREATE INDEX idx_usage_bucket_date ON daily_usage(bucket_id, usage_date);

-- Configurable cost rates
CREATE TABLE cost_rates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    rate        REAL NOT NULL,
    unit        TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Transfer queue with resume support
CREATE TABLE transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    object_key      TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('upload', 'download')),
    total_bytes     INTEGER,
    transferred     INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','in_progress','paused','completed','failed','cancelled')),
    upload_id       TEXT,
    local_path      TEXT NOT NULL,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_transfers_status ON transfers(status);

-- Individual parts for multipart uploads (resume support)
CREATE TABLE transfer_parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id     INTEGER NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
    part_number     INTEGER NOT NULL,
    offset          INTEGER NOT NULL,
    size            INTEGER NOT NULL,
    etag            TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','completed','failed')),
    UNIQUE(transfer_id, part_number)
);

-- Key-value store for app preferences and UI state
CREATE TABLE preferences (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- Schema version tracking for migrations
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Preferences Keys

| Key | Example Value | Purpose |
|---|---|---|
| `active_profile` | `"default"` | Current credential profile |
| `active_bucket` | `"my-videos"` | Current bucket |
| `last_s3_prefix` | `"videos/2025/"` | Last S3 path navigated to |
| `last_local_dir` | `"/Users/jane/Videos"` | Last local directory |
| `window_geometry` | `"(base64 QByteArray)"` | Window size and position |
| `window_state` | `"(base64 QByteArray)"` | Dock widget / toolbar state |
| `transfer_panel_visible` | `"true"` | Transfer panel collapsed state |
| `max_concurrent_transfers` | `"4"` | Transfer parallelism |
| `show_hidden_files` | `"false"` | Local pane hidden files |
| `completed_transfer_retention` | `"session"` | When to clear completed transfers |

### Migration Strategy

Migrations are numbered SQL files in `src/s3ui/db/migrations/`:

```
001_initial.sql
002_add_transfer_parts.sql
003_add_deep_archive_bytes.sql
```

On startup, the `Database` class:

1. Creates the database file if it doesn't exist
2. Enables WAL mode: `PRAGMA journal_mode=WAL`
3. Enables foreign keys: `PRAGMA foreign_keys=ON`
4. Reads the current `schema_version`
5. Applies any unapplied migrations in order
6. Records the new version in `schema_version`

---

## UI Design Notes

### Platform-Native Behavior

| Behavior | macOS | Windows | Linux |
|---|---|---|---|
| Keyboard modifier | Cmd | Ctrl | Ctrl |
| Keyboard shortcuts | Cmd+C, Cmd+V, Cmd+Backspace | Ctrl+C, Ctrl+V, Delete | Ctrl+C, Ctrl+V, Delete |
| Inline rename trigger | Enter (with selection) | F2 | F2 |
| File open | `QDesktopServices.openUrl` → Launch Services | `QDesktopServices.openUrl` → ShellExecute | `QDesktopServices.openUrl` → xdg-open |
| Drag-and-drop | Cocoa DnD via Qt | OLE DnD via Qt | X11/Wayland DnD via Qt |
| System notifications | NSUserNotificationCenter via Qt | WinRT Toast via Qt | org.freedesktop.Notifications via Qt |
| Font | System default (San Francisco) | System default (Segoe UI) | System default |
| File/folder icons | `QFileIconProvider` → system icons | `QFileIconProvider` → system icons | `QFileIconProvider` → theme icons |
| Window chrome | Native title bar | Native title bar | Native title bar |
| Menu bar | Global menu bar (top of screen) | In-window menu bar | In-window menu bar |

**No custom stylesheets.** The app must not call `setStyleSheet()` on any widget. PyQt6's default platform style (macOS: macintosh, Windows: windowsvista, Linux: fusion or platform native) handles everything. Custom styling makes apps look foreign.

**Exception:** The transfer progress bar may use a minimal stylesheet to set the bar color (green for uploads, blue for downloads) if the platform style doesn't support `QProgressBar` coloring via palette roles.

### Information Hierarchy

| Level | What's Shown | When |
|---|---|---|
| **Default** | Name, Size, Date Modified | Always visible in the file listing |
| **On hover** | Full file name tooltip (for truncated names) | Mouse hover on a table row |
| **On demand** | Storage class, ETag, content type, full S3 key, last modified (precise) | Right-click > Get Info |
| **Explicit** | Bucket stats, cost dashboard, rate table | Menu or toolbar button |

### Error Messages — Complete Table

| Scenario | User-Facing Message |
|---|---|
| No credentials configured | "No AWS account configured. Open Settings to add your credentials." |
| Invalid credentials | "These credentials aren't valid. Double-check your Access Key and Secret Key." |
| Expired credentials | "Your AWS credentials have expired. Update them in Settings." |
| Access denied (403) | "Permission denied. Your AWS account doesn't have access to this bucket." |
| Bucket not found (404) | "Bucket not found. It may have been deleted or the name may be wrong." |
| Object not found (404) | "This file no longer exists on S3. It may have been moved or deleted." |
| No internet connection | "Can't connect to AWS. Check your internet connection." |
| Connection timeout | "Connection timed out. AWS may be experiencing issues, or your network is slow." |
| Transfer interrupted | "Transfer interrupted. It will resume automatically when the connection is restored." |
| Disk full (download) | "Not enough disk space to download this file. Free up space and try again." |
| Source file deleted (upload) | "The source file was moved or deleted. This upload can't continue." |
| Name conflict (download) | "A file named 'X' already exists. Replace it, keep both, or skip?" |
| Bucket scan failed | "Couldn't scan this bucket. You may not have permission to list all objects." |
| Rate limit (429/503) | "AWS is throttling requests. The app will retry automatically." |
| SSL error | "Secure connection failed. Check your system clock and network settings." |
| S3 server error (500/503) | "AWS returned an error. This is usually temporary — the app will retry." |

---

## Cache & Sync Strategy

S3 has no change feed, no push notifications, and no conditional listing. You cannot ask "has this prefix changed since last time?" Every `list_objects_v2` call costs money ($0.005 per 1,000 requests) and adds 100–300ms of latency. The sync strategy must minimize API calls while keeping the UI feeling accurate and instant.

### Three Sync Scenarios

#### Scenario 1: Changes Made by the App (Most Common)

Every upload, delete, rename, move, and copy goes through our `S3Client` — we know exactly what changed. The model should never need to re-fetch from S3 to reflect our own operations.

**Approach: optimistic local mutation.**

When an operation completes, update the in-memory `S3ObjectModel` directly:

| Operation | Model Mutation | API Calls for Sync |
|---|---|---|
| Upload completes | `beginInsertRows()`, insert new `S3Item` at sorted position, `endInsertRows()` | 0 |
| Delete completes | `beginRemoveRows()`, remove item(s), `endRemoveRows()` | 0 |
| Rename completes | Update item's `name` and `key` in-place, emit `dataChanged()`, re-sort | 0 |
| Move out of current prefix | Remove from current listing | 0 |
| Move into current prefix | Insert into current listing (if prefix is cached) | 0 |
| New folder | Insert prefix-type `S3Item` | 0 |
| Copy to current prefix | Insert new item | 0 |

**Implementation detail — `S3ObjectModel` mutation API:**

```python
class S3ObjectModel(QAbstractTableModel):
    def insert_item(self, item: S3Item):
        """Insert an item in sorted position (prefixes first, then alpha)."""
        pos = bisect.bisect_left(self._items, item, key=self._sort_key)
        self.beginInsertRows(QModelIndex(), pos, pos)
        self._items.insert(pos, item)
        self.endInsertRows()
        self._update_footer()

    def remove_item(self, key: str):
        """Remove an item by its S3 key."""
        for i, item in enumerate(self._items):
            if item.key == key:
                self.beginRemoveRows(QModelIndex(), i, i)
                self._items.pop(i)
                self.endRemoveRows()
                self._update_footer()
                return

    def update_item(self, key: str, **fields):
        """Update fields on an existing item (e.g., after rename)."""
        for i, item in enumerate(self._items):
            if item.key == key:
                for k, v in fields.items():
                    setattr(item, k, v)
                left = self.index(i, 0)
                right = self.index(i, self.columnCount() - 1)
                self.dataChanged.emit(left, right)
                return
```

**What gets faked on optimistic insert:** When inserting an item after an upload completes, we don't have the exact `last_modified` or `etag` that S3 assigned. The app sets:
- `last_modified = datetime.now(timezone.utc)` (off by a few seconds at most)
- `etag = None` (hidden from the user by default, corrected on next real fetch)
- `size` = exact (we know the file size)
- `storage_class = "STANDARD"` (correct for new uploads)

This is invisible to the user since they never see etag, and a few-second timestamp error doesn't matter.

**Cache coherence:** Optimistic mutations must also update the prefix cache (Section below), so that navigating away and back doesn't revert to stale cached data while waiting for a background refresh.

#### Scenario 2: Navigating Between Prefixes

When the user clicks into a folder, the app needs its contents. If they've been there recently, we already have the data.

**Approach: stale-while-revalidate.**

```
User navigates to prefix
  │
  ├── Cache miss (never visited, or evicted)
  │     └── Show loading spinner
  │         Fetch from S3 in worker thread
  │         On response: populate model, cache result, hide spinner
  │
  └── Cache hit
        └── Show cached data immediately (0ms, no spinner)
            │
            ├── Cache age < 30 seconds
            │     └── Do nothing. Data is fresh enough.
            │
            └── Cache age >= 30 seconds
                  └── Background fetch in worker thread
                      On response:
                        ├── Results match cache → do nothing
                        └── Results differ → diff and apply
```

**Cache structure:**

```python
@dataclass
class CachedListing:
    prefix: str
    items: list[S3Item]
    fetched_at: float          # time.monotonic()
    dirty: bool = False        # set True after optimistic mutations

class ListingCache:
    _cache: OrderedDict[str, CachedListing]  # prefix → listing, LRU order
    _max_entries: int = 30
    _stale_seconds: float = 30.0

    def get(self, prefix: str) -> CachedListing | None
    def put(self, prefix: str, items: list[S3Item])
    def invalidate(self, prefix: str)          # remove one entry
    def invalidate_all(self)                   # clear everything
    def apply_mutation(self, prefix: str, fn: Callable[[list[S3Item]], list[S3Item]])
        """Apply an optimistic mutation to a cached listing."""
```

**Diff-and-apply on background refresh:**

When a background revalidation returns and the results differ from the cache, the model shouldn't `beginResetModel()` (which causes the view to lose scroll position and selection). Instead, compute a diff:

1. Build a dict of `key → S3Item` for both old and new listings
2. Items in new but not old → `insert_item()` (animate in)
3. Items in old but not new → `remove_item()` (animate out)
4. Items in both but with different size/date → `update_item()`

This keeps scroll position, selection, and any in-progress rename editing intact. The user sees items appear or disappear smoothly rather than the whole list flashing.

**Optimistic mutations update the cache too:**

When the app performs an upload to prefix `videos/2025/`, the cache entry for that prefix is also mutated. This ensures that if the user navigates away and back within the stale window, they see the optimistic data rather than the pre-mutation cached data.

```python
# After upload of "video.mp4" to "videos/2025/" completes:
new_item = S3Item(name="video.mp4", key="videos/2025/video.mp4", ...)
self._listing_cache.apply_mutation("videos/2025/", lambda items: sorted(items + [new_item], key=sort_key))
self._s3_model.insert_item(new_item)  # if currently viewing that prefix
```

#### Cache Race Condition: Background Revalidation vs. Optimistic Mutations

A race exists: the user uploads a file (optimistic insert into model + cache), then a background revalidation — triggered before the upload finished — returns older results that don't include the new file. A naive `diff_apply` would remove the optimistically-inserted item.

**Solution: dirty flag + mutation counter.**

Each `CachedListing` has a `dirty` flag (set `True` by `apply_mutation()`) and a `mutation_counter` (incremented on each optimistic mutation). When a background revalidation response arrives:

1. Check the mutation counter at response time vs. when the fetch was initiated.
2. If mutations happened during the fetch (`counter_now > counter_at_fetch_start`): **merge** the revalidation data with the current cache rather than replacing it. Specifically:
   - Items in the revalidation response but not in the current cache → insert (external addition detected)
   - Items in the current cache but not in the revalidation response → **keep** if they were optimistically added (have `dirty_source` marker), remove if they were just from the old cache
   - Items in both → update metadata from the revalidation response (gets the real `etag` and `last_modified`)
3. If no mutations happened during the fetch: standard `diff_apply` is safe.

This ensures optimistic mutations are never clobbered by a stale background fetch, while still picking up genuine external changes.

```python
@dataclass
class CachedListing:
    prefix: str
    items: list[S3Item]
    fetched_at: float
    dirty: bool = False
    mutation_counter: int = 0    # incremented on each optimistic mutation
```

#### Scenario 3: External Changes

If someone modifies the bucket from outside the app (AWS console, CLI, another tool), our cache is stale and we have no way to know.

**Approach: manual refresh + navigate refresh. No polling.**

- **Cmd+R / F5:** Re-fetches the current prefix from S3, bypasses cache entirely, replaces model contents.
- **Navigate:** Entering any prefix triggers a fetch (or background revalidation if cached). Simply navigating away and back is a refresh.
- **No background polling.** Polling would cost money per interval, drain battery on laptops, and add complexity for a case that rarely applies to the target user (single-person bucket).

For a shared bucket (multiple people writing to it), the user can hit refresh when they expect changes. This matches how every FTP client and most cloud file managers work.

### API Call Budget

Typical session — upload 20 files, organize into folders:

| Action | list calls | other calls |
|---|---|---|
| Launch, navigate to bucket root | 1 | — |
| Navigate into `videos/` | 1 | — |
| Navigate into `videos/2025/` | 1 | — |
| Upload 20 files | **0** | 20 put_object |
| Create folder `videos/2025/feb/` | **0** | 1 put_object |
| Move 10 files into that folder | **0** | 10 copy + 10 delete |
| Navigate back to `videos/2025/` | **0** (cached, <30s) | — |
| Navigate back to `videos/` | **0** (cached, <30s) | — |
| Wait 1 min, revisit `videos/2025/` | **1** (background) | — |
| **Session total** | **4** | 41 |

Without optimistic mutation, every operation batch would need a re-list — easily 15+ extra calls for the same session.

---

## UI Responsiveness

The app must feel instant. Every interaction responds within a single frame (16ms). Network latency is hidden behind background threads and optimistic UI updates. The user should never see a frozen window, a spinning cursor, or a button that doesn't respond.

### Core Rule: Nothing Blocks the Main Thread

The main thread runs the Qt event loop — painting, input handling, signal dispatch. Any work that might take >50ms must happen in a worker thread and communicate results back via signals.

| Work | Thread | Why |
|---|---|---|
| Widget layout, painting, input events | Main | Qt requirement — widgets are not thread-safe |
| `list_objects_v2` (100–500ms per page) | QThread | Network I/O |
| `put_object` / `get_object` / `upload_part` | QThreadPool worker | Network I/O, disk I/O |
| `head_object` (100–300ms) | QThread | Network I/O |
| Bucket scan (seconds to minutes) | QThread | Network I/O, potentially thousands of pages |
| Cost calculation | Main | CPU-only, reads from local SQLite, <1ms |
| SQLite reads | Main (or any thread) | Local disk, <1ms with WAL mode |
| SQLite writes | Any thread (mutex-serialized) | Local disk, <1ms per write |
| `QFileSystemModel` loading | Internal Qt thread | Qt manages this automatically |
| Icon resolution (`QFileIconProvider`) | Main | Fast (<1ms per icon), Qt caches results |
| Sort / filter on model data | Main | In-memory list operations, fast for <100K items |

### Perceived Performance Techniques

#### Instant Navigation

When the user clicks a folder:
1. **Frame 0 (0ms):** Breadcrumb bar updates immediately. Footer shows "Loading..."
2. **Frame 1 (16ms):** If cache hit, model is populated. User sees the listing. Done.
3. **If cache miss:** A subtle inline spinner appears (not a modal dialog, not a full-window overlay). The listing area is empty or shows a centered "Loading..." text. The user can still interact with the local pane, the toolbar, the transfer panel — nothing is blocked.
4. **On response (100–500ms later):** Model populates with `beginInsertRows` for each batch (or `beginResetModel` for the initial load). The spinner disappears.

**No modal loading dialogs for navigation.** The user should be able to click a folder, immediately click another folder (cancelling the first fetch), and the second fetch wins.

```python
class S3PaneWidget:
    _current_fetch_id: int = 0

    def navigate_to(self, prefix: str):
        self._current_fetch_id += 1
        fetch_id = self._current_fetch_id

        # Instant: update breadcrumbs and history
        self._breadcrumb.set_path(prefix)
        self._history.push(prefix)

        # Check cache
        cached = self._cache.get(prefix)
        if cached:
            self._model.set_items(cached.items)
            if cached.is_stale:
                self._background_revalidate(prefix, fetch_id)
            return

        # Cache miss — show spinner, fetch
        self._show_loading()
        self._fetch_listing(prefix, fetch_id)

    def _on_listing_ready(self, prefix: str, items: list[S3Item], fetch_id: int):
        # Ignore if user has already navigated elsewhere
        if fetch_id != self._current_fetch_id:
            return
        self._cache.put(prefix, items)
        self._model.set_items(items)
        self._hide_loading()
```

The `fetch_id` pattern ensures that if the user clicks three folders in quick succession, only the last fetch updates the view. Stale responses from earlier fetches are silently discarded.

#### Instant Feedback on Operations

Every user action must produce immediate visual feedback:

| Action | Immediate Feedback (0ms) | Background Work |
|---|---|---|
| Drop files to upload | Files appear in transfer panel as "Queued" | Upload starts |
| Upload completes | File appears in S3 listing (optimistic) | — |
| Click Delete + Confirm | Items fade out / disappear from listing | `delete_objects` call |
| Start rename (Enter/F2) | Cell becomes editable immediately | — |
| Commit rename | Name updates in listing immediately | `copy_object` + `delete_object` |
| Click Pause on transfer | Button changes to Resume, progress freezes | Worker stops between parts |
| Click bucket selector | Dropdown opens instantly | — |
| Select new bucket | Breadcrumb resets to `/`, listing clears | Fetch root listing |

**Delete with optimistic removal:**

Don't wait for the `delete_objects` API call to return before removing items from the model. Remove them immediately on user confirmation. If the delete fails (rare), re-insert the items and show an error.

```python
def _do_delete(self, items: list[S3Item]):
    # Optimistic: remove from model immediately
    for item in items:
        self._model.remove_item(item.key)
    self._cache.apply_mutation(self._current_prefix,
        lambda cached: [i for i in cached if i.key not in deleted_keys])

    # Background: actually delete
    worker = DeleteWorker(self._s3_client, self._bucket, [i.key for i in items])
    worker.signals.failed.connect(lambda keys, err: self._on_delete_failed(items, err))
    self._pool.start(worker)

def _on_delete_failed(self, items: list[S3Item], error: str):
    # Rollback: re-insert items
    for item in items:
        self._model.insert_item(item)
    self._show_error(f"Couldn't delete: {error}")
```

#### Smooth Transfer Progress

Transfer progress must update smoothly, not in jerky jumps:

- Progress signals fire every 500ms (not per-byte — that would flood the event loop)
- Speed is a rolling 3-second average (not instantaneous, which fluctuates wildly)
- ETA is smoothed: new ETA is blended with previous ETA to prevent jitter (`displayed_eta = 0.7 * new_eta + 0.3 * previous_eta`)
- The `QProgressBar` delegate repaints only the progress bar cell, not the entire row — use `dataChanged` with the specific model index, not `layoutChanged`

**Signal coalescing for batch operations:**

When uploading 50 files, 50 `transfer_progress` signals might fire within a single 16ms frame. The `TransferModel` should coalesce updates:

```python
class TransferModel(QAbstractTableModel):
    _pending_updates: dict[int, tuple[int, int]]  # transfer_id → (bytes, total)
    _update_timer: QTimer  # fires every 100ms

    def on_progress(self, transfer_id: int, bytes_done: int, total: int):
        self._pending_updates[transfer_id] = (bytes_done, total)
        # Don't emit dataChanged here — wait for timer

    def _flush_updates(self):
        """Called by timer every 100ms. Batch-emits dataChanged."""
        if not self._pending_updates:
            return
        for transfer_id, (bytes_done, total) in self._pending_updates.items():
            row = self._row_for_transfer(transfer_id)
            if row is not None:
                self._data[row].bytes_done = bytes_done
                self._data[row].total = total
        # Emit a single dataChanged covering all modified rows
        min_row = min(self._row_for_transfer(tid) for tid in self._pending_updates)
        max_row = max(self._row_for_transfer(tid) for tid in self._pending_updates)
        self.dataChanged.emit(
            self.index(min_row, 0),
            self.index(max_row, self.columnCount() - 1)
        )
        self._pending_updates.clear()
```

This limits transfer panel repaints to ~10 per second regardless of how many transfers are active.

#### Large Listings

For prefixes with thousands of objects:

- **Incremental population:** For listings >1000 items (multiple pages), populate the model after each page rather than waiting for all pages. The user sees the first 1000 items within 200ms and the rest stream in.

```python
def _on_listing_page(self, prefix: str, items: list[S3Item], is_first_page: bool, fetch_id: int):
    if fetch_id != self._current_fetch_id:
        return
    if is_first_page:
        self._model.set_items(items)  # reset + populate first page
        self._hide_loading()
    else:
        self._model.append_items(items)  # beginInsertRows for the new batch
    self._footer.setText(f"Loading... {self._model.rowCount()} objects")

def _on_listing_complete(self, prefix: str, fetch_id: int):
    if fetch_id != self._current_fetch_id:
        return
    self._footer.setText(f"{self._model.rowCount()} items, {format_size(self._model.total_size())}")
```

- **Virtual scrolling:** `QTableView` already virtualizes rendering — it only paints the visible rows. A table with 50,000 rows is no slower to paint than one with 50. No extra work needed here.

- **Sort performance:** Sorting 50,000 `S3Item`s by name is <10ms in Python (Timsort on a list of dataclasses). No concern.

- **Filter performance:** The `QSortFilterProxyModel` filters on every keystroke. For 50,000 items, `filterAcceptsRow` is called 50,000 times. With a simple `in` check on the name string, this is <20ms — fast enough. If profiling shows otherwise, debounce keystrokes to 100ms.

#### Avoiding Layout Thrash

Things that cause expensive full-view relayouts:

| Trigger | Cost | Mitigation |
|---|---|---|
| `beginResetModel()` | Full repaint, scroll reset, selection lost | Use only for initial load. Use row insert/remove/update for mutations. |
| `layoutChanged` | Full repaint, scroll preserved | Avoid. Use `dataChanged` for cell updates. |
| `dataChanged` on entire model | Repaints all visible cells | Scope to specific rows and columns. |
| Column resize | Repaints all visible rows | Qt handles this fine. No mitigation needed. |
| `setModel()` on a view | Full reset | Only call once during widget construction. |

**Rule: after initial load, never call `beginResetModel()` on `S3ObjectModel`.** All subsequent changes go through `insert_item`, `remove_item`, `update_item`, or the diff-and-apply path for background revalidation.

### Summary of Responsiveness Guarantees

| User Action | Max Time to Visual Response |
|---|---|
| Click / keyboard input | <16ms (same frame) |
| Navigate to cached prefix | <16ms (model swap from cache) |
| Navigate to uncached prefix | <16ms to show spinner; 100–500ms to populate |
| Upload/delete completes | <16ms (optimistic mutation) |
| Transfer progress update | <100ms (coalesced timer) |
| Start rename / new folder | <16ms (editor appears) |
| Open context menu | <16ms (Qt native menu) |
| Filter keystroke | <20ms (proxy filter on current listing) |
| Resize window / splitter | <16ms (Qt layout engine) |
| Switch bucket | <16ms to clear and show spinner; 100–500ms to populate |

---

## Key Dependencies

| Package | Version | Purpose | Size Impact |
|---|---|---|---|
| `PyQt6` | >=6.5 | UI framework, threading, signals | ~60 MB (wheels) |
| `boto3` | >=1.28 | AWS S3 SDK | ~80 MB (with botocore) |
| `keyring` | >=24.0 | OS-native credential storage | ~100 KB |
| `pyqtgraph` | >=0.13 | Lightweight charts (stats, cost) | ~10 MB |

**Total installed footprint:** ~150 MB

**Why not matplotlib?** matplotlib pulls in numpy (~30 MB) and has a larger footprint. pyqtgraph is built for Qt and renders directly to QGraphicsView — no image rasterization needed, smoother interaction, and smaller dependency.

**Why not PySide6?** PyQt6 and PySide6 are functionally near-identical. PyQt6 is chosen for its slightly more mature ecosystem and community support. The codebase should avoid PyQt6-specific APIs that don't exist in PySide6 in case a future switch is needed.

---

## Project Structure

```
s3ui/
├── docs/
│   └── design/
│       └── spec.md                     # This document
├── src/
│   └── s3ui/
│       ├── __init__.py                 # Package version
│       ├── app.py                      # QApplication setup, single-instance lock, main()
│       ├── main_window.py              # QMainWindow: layout, menus, toolbar, splitter
│       ├── constants.py                # App-wide constants: version, keyring service name, defaults
│       ├── ui/
│       │   ├── __init__.py
│       │   ├── local_pane.py           # LocalPaneWidget: QTreeView + QFileSystemModel + toolbar
│       │   ├── s3_pane.py              # S3PaneWidget: QTableView + S3ObjectModel + toolbar
│       │   ├── breadcrumb_bar.py       # BreadcrumbBar: clickable path segments, edit mode
│       │   ├── transfer_panel.py       # TransferPanelWidget: QTableView + TransferModel
│       │   ├── stats_dialog.py         # StatsDialog: bucket scan progress, charts, data tables
│       │   ├── cost_dialog.py          # CostDialog: cost breakdown, daily chart, export
│       │   ├── settings_dialog.py      # SettingsDialog: tabbed (credentials, transfers, rates, general)
│       │   ├── setup_wizard.py         # SetupWizard: QWizard with 3 pages
│       │   ├── confirm_delete.py       # DeleteConfirmDialog: file list, total size, warning
│       │   ├── name_conflict.py        # NameConflictDialog: Replace / Keep Both / Skip
│       │   └── get_info.py             # GetInfoDialog: S3 object metadata detail view
│       ├── core/
│       │   ├── __init__.py
│       │   ├── s3_client.py            # S3Client: boto3 wrapper, API call instrumentation
│       │   ├── listing_cache.py        # ListingCache: LRU cache with stale-while-revalidate
│       │   ├── transfers.py            # TransferEngine: QThreadPool, workers, queue management
│       │   ├── upload_worker.py        # UploadWorker: QRunnable, multipart upload logic
│       │   ├── download_worker.py      # DownloadWorker: QRunnable, ranged GET logic
│       │   ├── credentials.py          # CredentialStore: keyring CRUD, profile management
│       │   ├── cost.py                 # CostTracker: request counting, cost calculation
│       │   └── errors.py              # Error message mapping: boto3 exceptions → plain language
│       ├── models/
│       │   ├── __init__.py
│       │   ├── s3_objects.py           # S3ObjectModel: QAbstractTableModel for S3 listings
│       │   └── transfer_model.py       # TransferModel: QAbstractTableModel for transfer queue
│       └── db/
│           ├── __init__.py
│           ├── database.py             # Database: connection, WAL, migration runner
│           └── migrations/
│               └── 001_initial.sql     # Initial schema
├── tests/
│   ├── conftest.py                     # Shared fixtures: mock S3, temp SQLite
│   ├── test_s3_client.py              # S3Client wrapper tests (mocked boto3)
│   ├── test_transfers.py             # TransferEngine tests: queue, pause, resume, retry
│   ├── test_upload_worker.py          # Multipart upload logic, part tracking, resume
│   ├── test_download_worker.py        # Ranged GET logic, temp files, resume
│   ├── test_credentials.py           # CredentialStore tests (mocked keyring)
│   ├── test_cost.py                  # Cost calculation tests: formulas, edge cases
│   ├── test_db.py                    # Database migration tests
│   └── test_models.py               # S3ObjectModel, TransferModel signal tests
├── pyproject.toml                      # Build config, dependencies, entry point
├── LICENSE                             # MIT
└── README.md
```

---

## Build, Packaging & Publishing

### Versioning

Semantic versioning: `MAJOR.MINOR.PATCH`

- `MAJOR` — breaking changes (config format, database schema without migration)
- `MINOR` — new features (new dialogs, new operations)
- `PATCH` — bug fixes, dependency updates

The version is defined in one place: `src/s3ui/__init__.py`

```python
__version__ = "0.1.0"
```

`pyproject.toml` reads it dynamically:

```toml
[project]
dynamic = ["version"]

[tool.hatch.version]
path = "src/s3ui/__init__.py"
```

Git tags match the version: `v0.1.0`, `v0.2.0`, etc. All releases are tagged. CI builds are triggered by tags matching `v*`.

### pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "s3ui"
dynamic = ["version"]
description = "A native file manager for Amazon S3"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [
    { name = "S3UI Contributors" },
]
keywords = ["s3", "aws", "file-manager", "pyqt6", "desktop"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: X11 Applications :: Qt",
    "Environment :: MacOS X",
    "Environment :: Win32 (MS Windows)",
    "Intended Audience :: End Users/Desktop",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Desktop Environment :: File Managers",
    "Topic :: System :: Archiving",
]
dependencies = [
    "PyQt6>=6.5",
    "boto3>=1.28",
    "keyring>=24.0",
    "pyqtgraph>=0.13",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-qt>=4.2",
    "moto[s3]>=4.0",
    "ruff>=0.1",
    "pyinstaller>=6.0",
]

[project.scripts]
s3ui = "s3ui.app:main"

[project.urls]
Homepage = "https://github.com/OWNER/s3ui"
Documentation = "https://github.com/OWNER/s3ui/tree/main/docs"
Repository = "https://github.com/OWNER/s3ui"
Issues = "https://github.com/OWNER/s3ui/issues"

[tool.hatch.version]
path = "src/s3ui/__init__.py"

[tool.hatch.build.targets.sdist]
include = ["src/s3ui"]

[tool.hatch.build.targets.wheel]
packages = ["src/s3ui"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
qt_api = "pyqt6"
```

### Distribution Channels

| Channel | Format | Audience | Install Method |
|---|---|---|---|
| PyPI | sdist + wheel | Python users, developers | `pip install s3ui` |
| GitHub Releases | `.dmg`, `.exe`, `.AppImage` | End users (no Python needed) | Download and run |
| Homebrew (future) | Formula | macOS users | `brew install s3ui` |
| AUR (future) | PKGBUILD | Arch Linux users | `yay -S s3ui` |
| Flathub (future) | Flatpak | Linux users | `flatpak install s3ui` |

### PyPI Publishing

#### What Gets Published

A pure-Python source distribution (sdist) and wheel. The wheel contains only the Python source — no compiled binaries. Users install with `pip install s3ui` and pip resolves PyQt6/boto3/etc. as dependencies.

#### Publishing Workflow

Publishing is automated via GitHub Actions on tagged releases:

```yaml
name: Publish to PyPI
on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi          # GitHub environment with protection rules
    permissions:
      id-token: write          # for trusted publishing (no API tokens)
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install build
      - run: python -m build   # produces dist/s3ui-X.Y.Z.tar.gz and dist/s3ui-X.Y.Z-py3-none-any.whl
      - uses: pypa/gh-action-pypi-publish@release/v1
        # Uses PyPI trusted publishing — no API token needed.
        # Configure at pypi.org/manage/project/s3ui/settings/publishing/
```

**Trusted publishing:** PyPI supports GitHub OIDC — no API tokens stored in repository secrets. The GitHub Actions workflow identity is registered directly with PyPI as an authorized publisher. This is the most secure publishing method available.

**Pre-release publishing:** Tags matching `v*a*`, `v*b*`, or `v*rc*` (e.g., `v0.2.0a1`) are published as pre-releases on PyPI. The GitHub Action detects this automatically.

**TestPyPI:** During development, pushes to a `release/*` branch publish to TestPyPI first for validation:

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ s3ui
```

### Platform Builds (PyInstaller)

End users shouldn't need Python installed. PyInstaller bundles the app with a Python runtime and all dependencies into a standalone package.

#### PyInstaller Spec File

`build/s3ui.spec` — shared across platforms with OS-specific overrides:

```python
# build/s3ui.spec
import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ['../src/s3ui/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../src/s3ui/db/migrations', 's3ui/db/migrations'),
    ],
    hiddenimports=[
        'keyring.backends.macOS',       # macOS Keychain
        'keyring.backends.Windows',     # Windows Credential Locker
        'keyring.backends.SecretService', # Linux Secret Service
        'boto3',
        'botocore',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='S3UI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                     # No terminal window
    icon='build/icons/icon.ico' if sys.platform == 'win32' else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='S3UI',
)

# macOS .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='S3UI.app',
        icon='build/icons/icon.icns',
        bundle_identifier='com.s3ui.app',
        info_plist={
            'CFBundleShortVersionString': '0.1.0',
            'CFBundleName': 'S3UI',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.15',
            'NSRequiresAquaSystemAppearance': False,  # support dark mode
        },
    )
```

#### macOS Build

**Output:** `S3UI.app` inside `S3UI.dmg`

**Architecture:** Universal2 binary (Intel x86_64 + Apple Silicon arm64). Built on an Apple Silicon runner with `--target-arch universal2`.

**DMG creation:**

```bash
# After PyInstaller produces S3UI.app:
hdiutil create -volname "S3UI" -srcfolder dist/S3UI.app \
    -ov -format UDZO S3UI-$VERSION-macos.dmg
```

**Code signing and notarization:**

For distribution outside the Mac App Store, the app must be signed and notarized or macOS Gatekeeper will block it.

```bash
# Sign the .app bundle (requires Apple Developer ID certificate)
codesign --deep --force --verify --verbose \
    --sign "Developer ID Application: Your Name (TEAM_ID)" \
    --options runtime \
    --entitlements build/entitlements.plist \
    dist/S3UI.app

# Sign the .dmg
codesign --force --verify --verbose \
    --sign "Developer ID Application: Your Name (TEAM_ID)" \
    S3UI-$VERSION-macos.dmg

# Submit for notarization
xcrun notarytool submit S3UI-$VERSION-macos.dmg \
    --apple-id "$APPLE_ID" \
    --team-id "$TEAM_ID" \
    --password "$APP_SPECIFIC_PASSWORD" \
    --wait

# Staple the notarization ticket to the .dmg
xcrun stapler staple S3UI-$VERSION-macos.dmg
```

**Entitlements** (`build/entitlements.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>  <!-- required by Python -->
    <key>com.apple.security.cs.allow-jit</key>
    <true/>  <!-- required by Python -->
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>  <!-- required for bundled .dylibs -->
    <key>com.apple.security.network.client</key>
    <true/>  <!-- outbound HTTPS to S3 -->
</dict>
</plist>
```

**Without an Apple Developer certificate:** The app still works but users must right-click > Open on first launch to bypass Gatekeeper. The README documents this. A developer certificate ($99/year) is recommended once the project has traction.

**CI secrets for macOS signing:**

| Secret | Purpose |
|---|---|
| `MACOS_CERTIFICATE_P12` | Base64-encoded Developer ID certificate |
| `MACOS_CERTIFICATE_PASSWORD` | Password for the .p12 file |
| `APPLE_ID` | Apple ID email for notarization |
| `APPLE_TEAM_ID` | Team ID from Apple Developer portal |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password for notarization |

These are stored as GitHub Actions encrypted secrets and injected during the CI build.

#### Windows Build

**Output:** `S3UI-Setup-$VERSION.exe` (NSIS installer)

**PyInstaller produces** a `dist/S3UI/` directory with `S3UI.exe` and supporting DLLs.

**NSIS installer script** (`build/installer.nsi`):

```nsis
!include "MUI2.nsh"

Name "S3UI"
OutFile "S3UI-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\S3UI"
RequestExecutionLevel admin

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section "Install"
    SetOutPath "$INSTDIR"
    File /r "dist\S3UI\*.*"

    ; Start Menu shortcut
    CreateDirectory "$SMPROGRAMS\S3UI"
    CreateShortCut "$SMPROGRAMS\S3UI\S3UI.lnk" "$INSTDIR\S3UI.exe"
    CreateShortCut "$SMPROGRAMS\S3UI\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

    ; Desktop shortcut (optional, user can uncheck)
    CreateShortCut "$DESKTOP\S3UI.lnk" "$INSTDIR\S3UI.exe"

    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Add/Remove Programs registry entry
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayName" "S3UI"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayIcon" "$INSTDIR\S3UI.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "DisplayVersion" "${VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI" \
        "Publisher" "S3UI Contributors"
SectionEnd

Section "Uninstall"
    RMDir /r "$INSTDIR"
    RMDir /r "$SMPROGRAMS\S3UI"
    Delete "$DESKTOP\S3UI.lnk"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\S3UI"
SectionEnd
```

**Windows code signing:**

Optional but recommended. Without signing, Windows SmartScreen will warn "Unknown publisher."

```powershell
# Sign with a code signing certificate (EV or OV certificate from a CA)
signtool sign /f cert.pfx /p "$CERT_PASSWORD" /tr http://timestamp.digicert.com /td sha256 /fd sha256 S3UI-Setup.exe
```

**CI secrets for Windows signing:**

| Secret | Purpose |
|---|---|
| `WINDOWS_CERTIFICATE_PFX` | Base64-encoded code signing certificate |
| `WINDOWS_CERTIFICATE_PASSWORD` | Password for the .pfx file |

**Without a code signing certificate:** The installer still works but SmartScreen shows a warning on first run. Users click "More info" > "Run anyway." Documented in README.

**Portable mode alternative:** In addition to the installer, a `.zip` of the `dist/S3UI/` directory is also attached to the release. Users who don't want to install can extract and run `S3UI.exe` directly.

#### Linux Build

**Output:** `S3UI-$VERSION-x86_64.AppImage`

**AppImage creation:**

After PyInstaller produces the `dist/S3UI/` directory:

1. Create AppDir structure:

```
S3UI.AppDir/
├── usr/
│   ├── bin/
│   │   └── (all PyInstaller output files)
│   └── share/
│       ├── applications/
│       │   └── s3ui.desktop
│       └── icons/
│           └── hicolor/
│               ├── 256x256/apps/s3ui.png
│               └── scalable/apps/s3ui.svg
├── AppRun (symlink to usr/bin/S3UI)
├── s3ui.desktop
└── s3ui.png
```

2. Desktop entry (`s3ui.desktop`):

```ini
[Desktop Entry]
Type=Application
Name=S3UI
Comment=Native file manager for Amazon S3
Exec=S3UI
Icon=s3ui
Categories=Utility;FileManager;Network;
Terminal=false
StartupNotify=true
```

3. Package with `appimagetool`:

```bash
ARCH=x86_64 appimagetool S3UI.AppDir S3UI-$VERSION-x86_64.AppImage
```

**AppImage benefits:**
- Single file, no installation needed
- Works on any Linux distro with glibc >= 2.17
- No root access required
- User just downloads, `chmod +x`, and runs

**Limitations:**
- Desktop integration (file associations, start menu) requires the user to run `./S3UI-*.AppImage --appimage-extract` and install the `.desktop` file, or use AppImageLauncher
- No auto-update (consistent with our no-phoning-home principle)

### App Icons

Icons are needed in multiple formats:

| File | Format | Sizes | Used By |
|---|---|---|---|
| `build/icons/icon.icns` | Apple ICNS | 16–1024px | macOS .app bundle |
| `build/icons/icon.ico` | Windows ICO | 16–256px | Windows .exe, installer |
| `build/icons/icon.svg` | SVG | Scalable | Linux AppImage, Flathub |
| `build/icons/icon-256.png` | PNG | 256x256 | Linux AppImage fallback, GitHub |

All derived from a single source SVG. A build script generates the platform-specific formats:

```bash
# build/generate-icons.sh
# Requires: inkscape, iconutil (macOS), imagemagick

# PNG set from SVG
for size in 16 32 64 128 256 512 1024; do
    inkscape icon.svg -w $size -h $size -o icon-${size}.png
done

# macOS .icns
mkdir icon.iconset
cp icon-16.png icon.iconset/icon_16x16.png
cp icon-32.png icon.iconset/icon_16x16@2x.png
# ... (all required sizes)
iconutil -c icns icon.iconset -o icon.icns

# Windows .ico (multiple sizes in one file)
convert icon-16.png icon-32.png icon-48.png icon-64.png icon-128.png icon-256.png icon.ico
```

### CI/CD Pipeline (GitHub Actions) — Detailed

Three workflows:

#### 1. CI (every push and PR)

```yaml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install ruff
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/

  test:
    needs: lint
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: ${{ matrix.python }} }
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short
        env:
          QT_QPA_PLATFORM: offscreen   # headless Qt for CI
```

#### 2. Release Build (on version tags)

```yaml
name: Release
on:
  push:
    tags: ["v*"]

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short
        env: { QT_QPA_PLATFORM: offscreen }

      # Build universal2 .app
      - run: pyinstaller build/s3ui.spec --noconfirm

      # Code sign (if certificate is available)
      - name: Import signing certificate
        if: env.MACOS_CERTIFICATE_P12 != ''
        env:
          MACOS_CERTIFICATE_P12: ${{ secrets.MACOS_CERTIFICATE_P12 }}
          MACOS_CERTIFICATE_PASSWORD: ${{ secrets.MACOS_CERTIFICATE_PASSWORD }}
        run: |
          echo "$MACOS_CERTIFICATE_P12" | base64 -d > cert.p12
          security create-keychain -p "" build.keychain
          security import cert.p12 -k build.keychain -P "$MACOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
          security set-key-partition-list -S apple-tool:,apple: -k "" build.keychain
          security list-keychains -d user -s build.keychain

      - name: Sign and notarize
        if: env.MACOS_CERTIFICATE_P12 != ''
        run: build/scripts/sign-macos.sh

      # Create DMG
      - run: |
          hdiutil create -volname "S3UI" -srcfolder dist/S3UI.app \
              -ov -format UDZO "S3UI-${GITHUB_REF_NAME}-macos.dmg"

      - uses: actions/upload-artifact@v4
        with:
          name: macos-dmg
          path: "S3UI-*.dmg"

  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short
        env: { QT_QPA_PLATFORM: offscreen }

      # Build .exe
      - run: pyinstaller build/s3ui.spec --noconfirm

      # Create installer
      - uses: joncloud/makensis-action@v4
        with:
          script-file: build/installer.nsi
          arguments: "/DVERSION=${{ github.ref_name }}"

      # Also create portable .zip
      - run: Compress-Archive -Path dist/S3UI -DestinationPath "S3UI-${{ github.ref_name }}-windows-portable.zip"
        shell: pwsh

      # Code sign (if certificate is available)
      - name: Sign installer
        if: env.WINDOWS_CERTIFICATE_PFX != ''
        run: |
          echo "${{ secrets.WINDOWS_CERTIFICATE_PFX }}" > cert.b64
          certutil -decode cert.b64 cert.pfx
          signtool sign /f cert.pfx /p "${{ secrets.WINDOWS_CERTIFICATE_PASSWORD }}" /tr http://timestamp.digicert.com /td sha256 /fd sha256 "S3UI-Setup-${{ github.ref_name }}.exe"
        shell: bash

      - uses: actions/upload-artifact@v4
        with:
          name: windows-builds
          path: |
            S3UI-Setup-*.exe
            S3UI-*-portable.zip

  build-linux:
    runs-on: ubuntu-22.04     # pin to older Ubuntu for broader glibc compat
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y libxcb-xinerama0 libxkbcommon-x11-0 \
              fuse libfuse2  # required for AppImage

      - run: pip install -e ".[dev]"
      - run: pytest --tb=short
        env: { QT_QPA_PLATFORM: offscreen }

      # Build with PyInstaller
      - run: pyinstaller build/s3ui.spec --noconfirm

      # Package as AppImage
      - run: build/scripts/make-appimage.sh "${{ github.ref_name }}"

      - uses: actions/upload-artifact@v4
        with:
          name: linux-appimage
          path: "S3UI-*.AppImage"

  publish-pypi:
    runs-on: ubuntu-latest
    needs: [build-macos, build-windows, build-linux]
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1

  create-release:
    runs-on: ubuntu-latest
    needs: [build-macos, build-windows, build-linux, publish-pypi]
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with: { merge-multiple: true }

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
          files: |
            S3UI-*.dmg
            S3UI-Setup-*.exe
            S3UI-*-portable.zip
            S3UI-*.AppImage
```

#### 3. Nightly (optional, cron)

```yaml
name: Nightly
on:
  schedule:
    - cron: "0 6 * * *"   # 6 AM UTC daily

jobs:
  test:
    # Same matrix as CI — catches breakage from dependency updates
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short
        env: { QT_QPA_PLATFORM: offscreen }
```

### Release Checklist

Manual steps before tagging a release:

1. Update `__version__` in `src/s3ui/__init__.py`
2. Update `CFBundleShortVersionString` in `build/s3ui.spec` (macOS)
3. Commit: `git commit -m "Release vX.Y.Z"`
4. Tag: `git tag vX.Y.Z`
5. Push: `git push origin main --tags`
6. CI runs → builds all artifacts → publishes to PyPI → creates GitHub Release
7. Verify:
   - PyPI: `pip install s3ui==X.Y.Z` works
   - macOS: `.dmg` opens, app launches, Gatekeeper doesn't block (if signed)
   - Windows: installer runs, app launches, SmartScreen doesn't block (if signed)
   - Linux: AppImage runs on a clean Ubuntu VM
8. Edit the GitHub Release notes if the auto-generated ones need cleanup

### Estimated Bundle Sizes

| Artifact | Estimated Size | Contents |
|---|---|---|
| PyPI wheel | ~50 KB | Python source only (dependencies installed separately by pip) |
| macOS .dmg | ~120 MB | Python runtime + PyQt6 + boto3 + app code |
| Windows installer | ~100 MB | Python runtime + PyQt6 + boto3 + app code |
| Windows portable .zip | ~110 MB | Same, uncompressed |
| Linux AppImage | ~130 MB | Python runtime + PyQt6 + boto3 + system libs + app code |

The large size is dominated by PyQt6 (~60 MB) and boto3/botocore (~80 MB). UPX compression in PyInstaller reduces this by ~20%.

### Future Packaging Channels

#### Homebrew (macOS)

Once the project has stable releases, submit a formula to `homebrew-core` or maintain a tap:

```ruby
# homebrew-s3ui/Formula/s3ui.rb
class S3ui < Formula
  include Language::Python::Virtualenv

  desc "Native file manager for Amazon S3"
  homepage "https://github.com/OWNER/s3ui"
  url "https://files.pythonhosted.org/packages/.../s3ui-X.Y.Z.tar.gz"
  sha256 "..."
  license "MIT"

  depends_on "python@3.11"
  depends_on "pyqt@6"

  # ... virtualenv resource blocks for boto3, keyring, pyqtgraph
end
```

Users install with: `brew tap OWNER/s3ui && brew install s3ui`

Or, if accepted into homebrew-core: `brew install s3ui`

#### Flathub (Linux)

Flatpak manifest for broader Linux distribution:

```yaml
# com.s3ui.S3UI.yml
app-id: com.s3ui.S3UI
runtime: org.kde.Platform
runtime-version: "6.6"
sdk: org.kde.Sdk
command: s3ui
finish-args:
  - --share=network          # S3 access
  - --share=ipc              # X11 shared memory
  - --socket=fallback-x11
  - --socket=wayland
  - --filesystem=home         # local file access
  - --talk-name=org.freedesktop.secrets  # keyring
modules:
  - name: s3ui
    buildsystem: simple
    build-commands:
      - pip3 install --prefix=/app .
    sources:
      - type: archive
        url: https://files.pythonhosted.org/packages/.../s3ui-X.Y.Z.tar.gz
        sha256: "..."
```

#### AUR (Arch Linux)

```bash
# PKGBUILD
pkgname=s3ui
pkgver=X.Y.Z
pkgrel=1
pkgdesc="Native file manager for Amazon S3"
arch=('any')
url="https://github.com/OWNER/s3ui"
license=('MIT')
depends=('python' 'python-pyqt6' 'python-boto3' 'python-keyring' 'python-pyqtgraph')
makedepends=('python-build' 'python-installer' 'python-hatchling')
source=("https://files.pythonhosted.org/packages/.../s3ui-$pkgver.tar.gz")
sha256sums=('...')

build() {
    cd "s3ui-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "s3ui-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
```

#### Windows Package Managers

**winget:**

```yaml
# manifests/s/S3UI/S3UI/X.Y.Z/S3UI.S3UI.installer.yaml
PackageIdentifier: S3UI.S3UI
PackageVersion: X.Y.Z
InstallerType: nullsoft
Installers:
  - Architecture: x64
    InstallerUrl: https://github.com/OWNER/s3ui/releases/download/vX.Y.Z/S3UI-Setup-vX.Y.Z.exe
    InstallerSha256: "..."
```

Users install with: `winget install S3UI`

**Chocolatey:**

```powershell
# s3ui.nuspec
<?xml version="1.0"?>
<package>
  <metadata>
    <id>s3ui</id>
    <version>X.Y.Z</version>
    <title>S3UI</title>
    <authors>S3UI Contributors</authors>
    <description>Native file manager for Amazon S3</description>
    <tags>s3 aws file-manager</tags>
    <licenseUrl>https://github.com/OWNER/s3ui/blob/main/LICENSE</licenseUrl>
  </metadata>
</package>
```

Users install with: `choco install s3ui`

---

## Security Considerations

- **Credentials in keyring only.** Never in config files, SQLite, environment variables, or command-line arguments. The keyring backend is chosen by the `keyring` library based on the OS. On Linux, if no Secret Service provider is available, `keyring` falls back to an encrypted file — this is acceptable but the app should warn the user.
- **No credentials in logs.** The app does not log AWS keys, even at DEBUG level. The `S3Client` wrapper redacts credentials from any log output.
- **HTTPS only.** boto3 uses HTTPS by default. The app does not expose an option to disable TLS.
- **No telemetry.** Zero network calls except to the configured S3 endpoint.
- **No auto-update.** The app does not check for updates or download anything. Updates are manual (download new release from GitHub).
- **SQLite is local only.** Contains usage metrics and transfer state. No secrets.
- **Temp files for downloads.** Stored in the destination directory with a `.s3ui-download-` prefix. Cleaned up on completion. On cancel, the temp file is deleted.
- **Input validation.** Object key names from user input are validated: no null bytes, no keys exceeding 1024 bytes. Breadcrumb path input is sanitized (stripped of leading/trailing whitespace, collapsed double slashes).

### Minimum IAM Permissions

The setup wizard's "Test Connection" calls `ListBuckets`, which requires broad S3 read access. For ongoing operation, the app needs the following IAM policy. This should be documented in the README and shown as a copyable JSON snippet in the setup wizard's help tooltip.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3UIAccess",
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:CopyObject",
        "s3:ListMultipartUploadParts",
        "s3:ListBucketMultipartUploads",
        "s3:AbortMultipartUpload",
        "s3:CreateMultipartUpload",
        "s3:UploadPart",
        "s3:CompleteMultipartUpload"
      ],
      "Resource": [
        "arn:aws:s3:::*",
        "arn:aws:s3:::*/*"
      ]
    }
  ]
}
```

**Scoping down:** Users who want to restrict access to a single bucket can replace the `Resource` wildcards with their specific bucket ARN. The app should degrade gracefully if permissions are missing — e.g., if `ListAllMyBuckets` is denied, allow the user to type a bucket name manually instead of showing a dropdown. If `DeleteObject` is denied, the delete menu item should be disabled with a tooltip: "Your AWS credentials do not have delete permission."

**Error handling for permission denials:** When any S3 API call returns `AccessDenied`, the error translation layer maps it to a plain-language message: "Access denied. Your AWS credentials don't have permission for this action. Check your IAM policy." The raw error code is available behind "Show Details."

---

## Testing Strategy

| Category | Tool | Coverage Target |
|---|---|---|
| S3 operations | `moto` (mock S3) + `pytest` | All S3Client methods, pagination, error handling |
| Transfer engine | `moto` + `pytest` | Queue ordering, pause/resume, retry, multipart, resume after crash |
| Cost calculation | `pytest` | All rate formulas, edge cases (zero usage, tier boundaries) |
| Database | `pytest` with temp SQLite | Migrations, CRUD, concurrent access |
| UI widgets | `pytest-qt` | Widget creation, signal emission, model updates |
| Integration | `moto` + `pytest-qt` | Full upload/download flow through UI |

**Mocking strategy:**

- `moto` provides a full in-memory S3 mock. Tests create buckets and objects in moto, then run S3Client operations against it.
- `keyring` is mocked with a dictionary backend in tests (no real OS keyring access).
- `QFileSystemModel` is tested against a temp directory with known files.

---

## Competitive Landscape Summary

| Feature | S3UI | Cyberduck | Commander One | ExpanDrive | CloudMounter |
|---|---|---|---|---|---|
| Free & open source | Yes | Yes (no dual pane) | No ($30) | No ($50/yr) | No ($45) |
| Cross-platform | Yes | macOS/Win | macOS only | Yes | macOS/Win |
| Dual-pane (local + S3) | Yes | No | Yes | No (mount) | No (mount) |
| Large file resume | Yes | No | Partial | No | No |
| Persistent transfer queue | Yes | No | No | N/A | N/A |
| Cost tracking | Yes | No | No | No | No |
| Non-technical user friendly | Yes | No | No | Partial | Partial |

---

## Future Considerations (Out of Scope for v1)

- S3-compatible providers (MinIO, Backblaze B2) — partially supported via custom endpoint URL in settings
- Bucket policy / ACL editor
- Presigned URL generation and sharing ("Share link" in context menu)
- Object versioning browser (list and restore previous versions)
- Sync mode (local folder <-> S3 prefix, bidirectional, like Dropbox)
- CloudWatch metrics integration for more accurate cost data
- Video thumbnail generation for the S3 pane (using ranged requests to extract a frame)
- Image/video preview panel without downloading the full file
- Drag-and-drop between two S3UI windows (different buckets/accounts)
- Touch Bar support on macOS (upload/download shortcuts)
- System tray mode (minimize to tray, show transfer progress in tray icon)
