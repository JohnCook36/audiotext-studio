# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: запускать НА WINDOWS, из корня проекта.
from PyInstaller.utils.hooks import collect_all

binaries = []
datas = [('assets/app.ico', 'assets')]
hiddenimports = []
for module in (
    'ctranslate2', 'faster_whisper', 'onnxruntime', 'av', 'tokenizers',
    'imageio_ffmpeg', 'huggingface_hub',
):
    extra_datas, extra_binaries, extra_hidden = collect_all(module)
    datas.extend(extra_datas)
    binaries.extend(extra_binaries)
    hiddenimports.extend(extra_hidden)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['pytest', 'IPython', 'matplotlib', 'scipy.tests', 'torch', 'tensorflow'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='AudioTextStudio',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/app.ico',
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='AudioTextStudio',
)