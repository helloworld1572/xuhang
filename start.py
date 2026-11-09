"""python start.py --port 8765"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

import uvicorn
from server import create_app
from network import lan_addresses


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="启动续航局域网接续服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    data_dir = Path(os.environ.get("XUHANG_DATA_DIR", Path(__file__).parent / "data"))
    app = create_app(data_dir)
    code = (data_dir / "access-code.txt").read_text().strip()
    print(f"\n续航 v0.1 已准备启动\n电脑：http://127.0.0.1:{args.port}\n项目访问码：{code}", flush=True)
    for ip in lan_addresses():
        print(f"手机候选地址：http://{ip}:{args.port}（选择 Wi-Fi 网卡地址）", flush=True)
    print("电脑和手机连接同一 Wi-Fi。保持电脑开机和服务运行。Ctrl+C 停止。\n", flush=True)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{args.port}/api/health", timeout=1) as response:
            if json.load(response).get("app") == "xuhang":
                print("续航服务已经在运行，请直接打开上面的地址。", flush=True)
                raise SystemExit(0)
    except OSError:
        pass
    (data_dir / "server.pid").write_text(str(os.getpid()), encoding="ascii")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)
