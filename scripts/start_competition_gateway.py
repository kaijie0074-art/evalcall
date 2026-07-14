#!/usr/bin/env python3
"""启动受保护的 EvalCall 实时服务、临时 HTTPS 隧道并刷新固定比赛入口。"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = Path.home() / ".evalcall" / "public-access-token"
SITE = ROOT / "site-deploy"
PORT = 8766
PUBLIC_ENDPOINT = "https://kaijie0074-art.github.io/evalcall/live-endpoint.json"
PUBLIC_ENTRY = "https://kaijie0074-art.github.io/evalcall/live.html"
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def request_json(url: str, timeout: float = 8) -> dict:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "EvalCall-Final-Demo"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed competition endpoints only
        return json.loads(response.read())


def ensure_token() -> str:
    if TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = "evalcall-final-" + secrets.token_urlsafe(28)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return token


def local_public_server_ready() -> bool:
    try:
        health = request_json(f"http://127.0.0.1:{PORT}/api/health", timeout=2)
        return bool(health.get("ok") and health.get("backend_available") and health.get("public_access_protected"))
    except Exception:  # noqa: BLE001 - readiness probe
        return False


def start_server(token: str, log_dir: Path) -> subprocess.Popen[str] | None:
    if local_public_server_ready():
        print(f"[1/4] 本地实时服务已就绪：127.0.0.1:{PORT}")
        return None
    env = os.environ.copy()
    env.update(
        {
            "EVALCALL_PUBLIC_ACCESS_TOKEN": token,
            "EVALCALL_PUBLIC_POSTS_PER_15M": "90",
            "PYTHONUNBUFFERED": "1",
        }
    )
    log_path = log_dir / "public-server.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(  # noqa: S603 - fixed local command
        [sys.executable, "-m", "evalcall", "demo", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(45):
        if process.poll() is not None:
            raise RuntimeError(f"本地实时服务启动失败，请查看 {log_path}")
        if local_public_server_ready():
            print(f"[1/4] 本地实时服务已就绪：127.0.0.1:{PORT}")
            return process
        time.sleep(1)
    process.terminate()
    raise RuntimeError(f"本地实时服务 45 秒内未通过真实后端健康检查，请查看 {log_path}")


def start_tunnel() -> tuple[subprocess.Popen[str], str]:
    env = os.environ.copy()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    process = subprocess.Popen(  # noqa: S603 - fixed cloudflared command
        [
            "cloudflared",
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--url",
            f"http://127.0.0.1:{PORT}",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + 40
    assert process.stdout is not None
    while time.time() < deadline:
        line = process.stdout.readline()
        if process.poll() is not None:
            raise RuntimeError("HTTPS 隧道启动失败：" + line.strip())
        match = TUNNEL_PATTERN.search(line)
        if match:
            url = match.group(0)
            print("[2/4] HTTPS 实时通道已建立")
            return process, url
    process.terminate()
    raise RuntimeError("40 秒内未取得 HTTPS 隧道地址")


def run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git executable with controlled arguments
        ["git", *args], cwd=cwd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )


def publish_endpoint(live_url: str) -> None:
    run_git(["fetch", "origin", "gh-pages"], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="evalcall-ghpages-") as temp:
        worktree = Path(temp) / "site"
        run_git(["worktree", "add", "--detach", str(worktree), "origin/gh-pages"], cwd=ROOT)
        try:
            payload = {
                "live_url": live_url,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "mode": "现场演示机实时服务",
                "fallback_url": "app.html",
            }
            (worktree / "live-endpoint.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            shutil.copy2(SITE / "live.html", worktree / "live.html")
            run_git(["add", "live.html", "live-endpoint.json"], cwd=worktree)
            committed = run_git(
                ["commit", "-m", "Deploy competition live gateway"], cwd=worktree, check=False
            )
            if committed.returncode == 0:
                run_git(["push", "origin", "HEAD:gh-pages"], cwd=worktree)
        finally:
            run_git(["worktree", "remove", "--force", str(worktree)], cwd=ROOT, check=False)

    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            published = request_json(PUBLIC_ENDPOINT + f"?ts={time.time_ns()}", timeout=8)
            if published.get("live_url") == live_url:
                print("[3/4] 固定比赛入口已更新")
                return
        except Exception:  # noqa: BLE001 - deployment propagation probe
            pass
        time.sleep(4)
    raise RuntimeError("GitHub Pages 两分钟内未刷新；请直接使用终端显示的实时通道地址")


def main() -> int:
    os.chdir(ROOT)
    token = ensure_token()
    log_dir = ROOT / "runs" / "competition_gateway"
    log_dir.mkdir(parents=True, exist_ok=True)
    server: subprocess.Popen[str] | None = None
    tunnel: subprocess.Popen[str] | None = None
    caffeinate: subprocess.Popen[str] | None = None
    try:
        server = start_server(token, log_dir)
        tunnel, live_url = start_tunnel()
        publish_endpoint(live_url)
        try:
            health = request_json(live_url + "/api/health", timeout=15)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"公网真实健康检查失败：{exc}") from exc
        if not health.get("backend_available") or not health.get("public_access_protected"):
            raise RuntimeError("公网服务存在，但模型后端或访问保护未就绪")
        print("[4/4] 公网真实后端验收通过")
        webbrowser.open(PUBLIC_ENTRY + "?access=" + urllib.parse.quote(token))
        if shutil.which("caffeinate"):
            caffeinate = subprocess.Popen(["caffeinate", "-dimsu"])  # noqa: S603,S607 - macOS system utility
        print("\n比赛入口已打开。请保持本窗口、电脑和网络在线；按 Ctrl+C 才会停止实时通道。")
        while tunnel.poll() is None:
            time.sleep(5)
        raise RuntimeError("HTTPS 隧道意外退出")
    except KeyboardInterrupt:
        print("\n正在关闭比赛实时通道……")
        return 0
    except Exception as exc:  # noqa: BLE001 - concise operator-facing failure
        print(f"\n启动失败：{exc}", file=sys.stderr)
        print(f"兜底入口：{PUBLIC_ENTRY.replace('live.html', 'app.html')}", file=sys.stderr)
        return 1
    finally:
        for process in (caffeinate, tunnel, server):
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())
