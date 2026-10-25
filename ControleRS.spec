# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller — exécutable Windows autonome (un seul fichier, sans console).
# Build : build_exe.bat  (ou : pyinstaller --noconfirm ControleRS.spec)
import os

# Référentiel fournisseurs initial (copié dans %APPDATA%\ControleRS au 1er lancement).
# Non versionné (contient des noms réels) : inclus seulement s'il existe localement,
# sinon l'exe démarre avec un référentiel vide.
datas = []
if os.path.exists('supplier_registry.json'):
    datas.append(('supplier_registry.json', '.'))
if os.path.isdir('assets'):
    datas.append(('assets', 'assets'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Modules Qt/Python inutilisés par l'application : exclus pour réduire la taille de l'exe.
    excludes=[
        'tkinter', 'matplotlib', 'IPython', 'pytest',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.Qt3DCore',
        'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtPdf', 'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtBluetooth',
        'PySide6.QtPositioning', 'PySide6.QtSerialPort', 'PySide6.QtDesigner',
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
    name='ControleRS',
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
