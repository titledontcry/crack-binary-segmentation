"""Isolated browser-test server: copies database records; never edits the real archive."""
import os
import sqlite3
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="crack-workspace-preview-") as temporary:
    os.environ["CRACK_DATA_ROOT"] = temporary
    import sys
    sys.path.insert(0, str(ROOT))
    from drone_inference import app as m
    source = ROOT / "drone_inference/data/20261010_134920"
    if source.is_dir():
        target = Path(temporary)/source.name
        target.mkdir()
        with sqlite3.connect(f"{(source/'inspection.sqlite3').as_uri()}?mode=ro",uri=True) as src:
            with sqlite3.connect(target/'inspection.sqlite3') as dst:
                src.backup(dst)
        (target/'originals').symlink_to(source/'originals',target_is_directory=True)
        # Individual existing result files are read-only references. New reviewed masks
        # are created in the temporary result directories, not in the real archive.
        for image_dir in (source/'results').iterdir():
            if image_dir.is_dir():
                local = target/'results'/image_dir.name
                local.mkdir(parents=True)
                for file in image_dir.iterdir():
                    if file.is_file():
                        (local/file.name).symlink_to(file)
    m.worker = lambda: None
    m.preload_model = lambda: None
    uvicorn.run(m.app,host="127.0.0.1",port=8766,log_level="warning")
