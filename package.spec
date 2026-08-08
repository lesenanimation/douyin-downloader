# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 打包为独立 exe，无终端黑窗。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent

a = Analysis(
    ["client.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ("web", "web"),
    ],
    hiddenimports=[
        # async I/O
        "aiohttp", "aiofiles", "aiosqlite", "httpx",
        # UI
        "rich", "yaml", "dateutil",
        # crypto
        "gmssl",
        # flask
        "flask", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker",
        # PyQt6
        "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebChannel", "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineQuick",
        # tools
        "tools.cookie_fetcher", "utils.cookie_utils",
        # storage
        "storage.database", "storage.file_manager",
        # config
        "config.config_loader", "config.default_config",
        # core
        "core.api_client", "core.downloader_factory", "core.downloader_base",
        "core.url_parser", "core.video_downloader",
        # control
        "control.rate_limiter",
        # cli
        "cli.main",
        # sub-modules
        "core.user_modes", "core.user_modes.post_strategy",
        "core.user_modes.collect_strategy",
        "core.user_modes.like_strategy",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "PIL", "cv2", "torch", "tensorflow",
        "test", "tests", "unittest", "pytest",
        "setuptools", "pip",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="抖音下载器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # ← 无黑窗！
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico" if (ROOT / "icon.ico").exists() else None,
)
