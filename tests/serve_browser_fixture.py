"""Disposable browser-test service; never opens the user's data directory."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from server import create_app

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='xuhang-browser-') as folder:
        uvicorn.run(create_app(Path(folder), 'BROWSER-TEST-ONLY'), host='127.0.0.1', port=8876, access_log=False)
