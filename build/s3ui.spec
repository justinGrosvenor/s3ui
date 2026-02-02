# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for S3UI — cross-platform build configuration.
# Run from project root: pyinstaller build/s3ui.spec

import os
import re
import sys

block_cipher = None

# Project root is one level up from the spec file directory
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# Read version dynamically from src/s3ui/__init__.py
with open(os.path.join(ROOT, 'src', 's3ui', '__init__.py')) as f:
    version = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read()).group(1)

a = Analysis(
    [os.path.join(ROOT, 'src', 's3ui', 'app.py')],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'src', 's3ui', 'db', 'migrations'), 's3ui/db/migrations'),
        (os.path.join(ROOT, 'src', 's3ui', 'resources'), 's3ui/resources'),
    ],
    hiddenimports=[
        'keyring.backends.macOS',
        'keyring.backends.Windows',
        'keyring.backends.SecretService',
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
    console=False,
    icon=os.path.join(ROOT, 'build', 'icons', 'icon.ico') if sys.platform == 'win32' else None,
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
        icon=os.path.join(ROOT, 'build', 'icons', 'icon.icns'),
        bundle_identifier='com.s3ui.app',
        info_plist={
            'CFBundleShortVersionString': version,
            'CFBundleName': 'S3UI',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.15',
            'NSRequiresAquaSystemAppearance': False,
        },
    )
