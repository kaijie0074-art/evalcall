#!/usr/bin/env python3
"""启动开放的 EvalCall 实时服务、临时 HTTPS 隧道并刷新固定比赛入口。"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site-deploy"
PORT = 8766
PUBLIC_ENDPOINT = "https://kaijie0074-art.github.io/evalcall/live-endpoint.json"
PUBLIC_ENTRY = "https://kaijie0074-art.github.io/evalcall/app.html"
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def request_json(url: str, timeout: float = 8) -> dict:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "EvalCall-Final-Demo"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed competition endpoints only
        return json.loads(response.read())


def local_public_server_ready() -> bool:
    try:
        health = request_json(f"http://127.0.0.1:{PORT}/api/health", timeout=2)
        return bool(health.get("ok") and health.get("public_access") == "open")
    except Exception:  # noqa: BLE001 - readiness probe
        return False


def start_server(log_dir: Path) -> subprocess.Popen[str] | None:
    if local_public_server_ready():
        print(f"[1/4] 本地实时服务已就绪：127.0.0.1:{PORT}")
        return None
    env = os.environ.copy()
    env.update(
        {
            "EVALCALL_CODEX_TIMEOUT": "60",
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
    raise RuntimeError(f"本地实时服务 45 秒内未通过开放访问健康检查，请查看 {log_path}")


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
            "auto",
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
            current_url_file = ROOT / "runs" / "competition_gateway" / "current-live-url.txt"
            current_url_file.parent.mkdir(parents=True, exist_ok=True)
            current_url_file.write_text(url + "\n", encoding="utf-8")
            # 继续排空 cloudflared 输出，避免长时间运行后管道写满导致隧道卡死。
            threading.Thread(
                target=lambda: [None for _ in process.stdout],
                name="cloudflared-log-drain",
                daemon=True,
            ).start()
            health_deadline = time.time() + 75
            while time.time() < health_deadline and process.poll() is None:
                try:
                    health = request_json(url + "/api/health", timeout=8)
                    if health.get("ok") and health.get("public_access") == "open":
                        print("[2/4] HTTPS 实时通道已建立并通过公网健康检查")
                        return process, url
                except Exception:  # noqa: BLE001 - Quick Tunnel needs propagation time
                    pass
                time.sleep(3)
            process.terminate()
            raise RuntimeError("HTTPS 隧道已取得地址，但 75 秒内未通过公网健康检查")
    process.terminate()
    raise RuntimeError("40 秒内未取得 HTTPS 隧道地址")


def run_git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    attempts = 3 if check else 1
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(attempts):
        result = subprocess.run(  # noqa: S603 - fixed git executable with controlled arguments
            ["git", "-c", "http.version=HTTP/1.1", *args],
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
        if result.returncode == 0 or not check:
            return result
        if attempt + 1 < attempts:
            time.sleep(2**attempt)
    assert result is not None
    output = result.stdout.strip() or f"exit code {result.returncode}"
    raise RuntimeError(f"git {' '.join(args)} 连续 {attempts} 次失败：{output}")


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
            shutil.copy2(SITE / "app.html", worktree / "app.html")
            shutil.copy2(SITE / "demo-cache.js", worktree / "demo-cache.js")
            shutil.copy2(SITE / "product-shell.css", worktree / "product-shell.css")
            shutil.copy2(SITE / "product-shell.js", worktree / "product-shell.js")
            shutil.copy2(SITE / "b2b-pattern-workbench.html", worktree / "b2b-pattern-workbench.html")
            shutil.copy2(SITE / "reference-replicas.html", worktree / "reference-replicas.html")
            component_target = worktree / "component-library"
            component_target.mkdir(exist_ok=True)
            component_assets = [
                "evalcall-selected-theme.css",
                "b2b-operations-patterns.json",
                "b2b-pattern-workbench.css",
                "b2b-pattern-workbench.js",
                "reference-replicas.css",
                "reference-replicas.js",
            ]
            for asset in component_assets:
                shutil.copy2(SITE / "component-library" / asset, component_target / asset)
            reference_target = worktree / "reference-assets"
            reference_target.mkdir(exist_ok=True)
            for asset in (
                "linear-my-issues.png",
                "vanta-risk-register.png",
                "retool-admin-panel.png",
            ):
                shutil.copy2(SITE / "reference-assets" / asset, reference_target / asset)
            run_git(
                [
                    "add",
                    "app.html",
                    "live.html",
                    "live-endpoint.json",
                    "demo-cache.js",
                    "product-shell.css",
                    "product-shell.js",
                    "b2b-pattern-workbench.html",
                    "reference-replicas.html",
                    *[f"component-library/{asset}" for asset in component_assets],
                    "reference-assets/linear-my-issues.png",
                    "reference-assets/vanta-risk-register.png",
                    "reference-assets/retool-admin-panel.png",
                ],
                cwd=worktree,
            )
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


def publish_endpoint_with_retry(live_url: str, tunnel: subprocess.Popen[str]) -> None:
    """网络短暂波动时保持隧道在线，反复刷新固定入口。"""
    last_error: Exception | None = None
    for attempt in range(4):
        if tunnel.poll() is not None:
            raise RuntimeError("固定入口更新前 HTTPS 隧道已退出")
        try:
            publish_endpoint(live_url)
            return
        except Exception as exc:  # noqa: BLE001 - transient GitHub/network failures are retried
            last_error = exc
            if attempt < 3:
                wait_seconds = 5 * (attempt + 1)
                print(f"[3/4] 固定入口更新暂未成功，{wait_seconds} 秒后自动重试：{exc}")
                time.sleep(wait_seconds)
    raise RuntimeError(f"固定入口连续更新失败：{last_error}")


def main() -> int:
    os.chdir(ROOT)
    log_dir = ROOT / "runs" / "competition_gateway"
    log_dir.mkdir(parents=True, exist_ok=True)
    server: subprocess.Popen[str] | None = None
    tunnel: subprocess.Popen[str] | None = None
    caffeinate: subprocess.Popen[str] | None = None
    try:
        server = start_server(log_dir)
        tunnel, live_url = start_tunnel()
        publish_endpoint_with_retry(live_url, tunnel)
        try:
            health = request_json(live_url + "/api/health", timeout=15)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"公网真实健康检查失败：{exc}") from exc
        if health.get("public_access") != "open":
            raise RuntimeError("公网服务存在，但开放访问状态未就绪")
        backend_text = "可用" if health.get("backend_available") else "仍在探测或暂不可用"
        print(f"[4/4] 公网开放访问验收通过；模型后端：{backend_text}")
        if not os.environ.get("EVALCALL_NO_BROWSER"):
            webbrowser.open(PUBLIC_ENTRY)
        if shutil.which("caffeinate"):
            caffeinate = subprocess.Popen(["caffeinate", "-dimsu"])  # noqa: S603,S607 - macOS system utility
        print("\n比赛入口已打开。请保持本窗口、电脑和网络在线；按 Ctrl+C 才会停止实时通道。")
        local_failures = 0
        public_failures = 0
        next_public_probe = 0.0
        while tunnel.poll() is None:
            time.sleep(5)
            if not local_public_server_ready():
                local_failures += 1
                if local_failures >= 3:
                    raise RuntimeError("本地实时服务连续三次健康检查失败，交由系统自动重启")
            elif time.time() >= next_public_probe:
                local_failures = 0
                next_public_probe = time.time() + 30
                try:
                    public_health = request_json(live_url + "/api/health", timeout=12)
                    if public_health.get("public_access") == "open":
                        public_failures = 0
                    else:
                        public_failures += 1
                except Exception:  # noqa: BLE001 - watchdog restarts the entire gateway
                    public_failures += 1
            else:
                local_failures = 0
            # 公网每 30 秒探测一次，允许约 5 分钟的临时网络波动。
            if public_failures >= 10:
                raise RuntimeError("公网实时通道连续十次健康检查失败，交由系统自动重启")
        raise RuntimeError("HTTPS 隧道意外退出")
    except KeyboardInterrupt:
        print("\n正在关闭比赛实时通道……")
        return 0
    except Exception as exc:  # noqa: BLE001 - concise operator-facing failure
        print(f"\n启动失败：{exc}", file=sys.stderr)
        print(f"统一 Demo 入口：{PUBLIC_ENTRY}", file=sys.stderr)
        return 1
    finally:
        for process in (caffeinate, tunnel, server):
            if process and process.poll() is None:
                process.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    raise SystemExit(main())
