#!/usr/bin/env python3
"""wangsiEDA workstation launcher (stdlib only).

  python wangsi.py up       ensure the service runs on the compute host, open an SSH tunnel, open the console (default)
  python wangsi.py deploy   push ./cloud to the compute host, run the self-test, restart the service
  python wangsi.py status   service + host status
  python wangsi.py demo     run the bundled synthetic demo on THIS machine (no compute host, no private data; macOS/Linux)
  python wangsi.py doctor   install report from the compute host (what works, what is missing, how to fix)
  python wangsi.py mcp      print the MCP client config for this install

All computation, EDA tools, model calls and data stay on the compute host; this machine only holds the tunnel.
"""
import io, json, os, shlex, shutil, socket, subprocess, sys, tarfile, time, webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SSH = os.environ.get("WANGSI_SSH") or shutil.which("ssh") or "C:/Windows/System32/OpenSSH/ssh.exe"
HOST = os.environ.get("WANGSI_HOST", "your-ssh-alias")
REMOTE = os.environ.get("WANGSI_REMOTE", "/path/to/wangsieda")
RQ = shlex.quote(REMOTE)
# 部署时不覆盖计算主机上的站点文件（配置、提示词、运行数据）
NO_DEPLOY = ("wangsi_config.json", "var", "jobs", "derived", "demo_root")


def preflight():
    if HOST == "your-ssh-alias" or REMOTE == "/path/to/wangsieda":
        sys.exit("先设置环境变量 WANGSI_HOST（SSH 别名）和 WANGSI_REMOTE（计算主机上的安装目录）。")
    if not (Path(SSH).exists() or shutil.which(SSH)):
        sys.exit(f"找不到 ssh：{SSH}。安装 OpenSSH 客户端，或用 WANGSI_SSH 指定路径。")
PORT = int(os.environ.get("WANGSI_PORT", "18765"))
OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15", "-o", "LogLevel=ERROR"]
NOWIN = 0x08000000 if os.name == "nt" else 0


def ssh(cmd, data=None, timeout=120):
    p = subprocess.run([SSH] + OPTS + [HOST, cmd], input=data, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace").strip(), p.stderr.decode("utf-8", "replace").strip()


def port_open(port):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def deploy():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for p in (HERE / "cloud").rglob("*"):
            rel = p.relative_to(HERE / "cloud")
            if rel.parts[0] in NO_DEPLOY or (len(rel.parts) == 2 and rel.parts[0] == "prompts" and p.suffix == ".txt"):   # keep prompts/default/
                continue
            if p.is_file() and "__pycache__" not in p.parts:
                tf.add(p, arcname=p.relative_to(HERE / "cloud").as_posix())
    ctl = shlex.quote(REMOTE + "/bin/wangsictl")
    rc, out, err = ssh(f"mkdir -p {RQ} && tar -C {RQ} -xf - && chmod +x {ctl} && {ctl} selftest && {ctl} restart && {ctl} doctor",
                       data=buf.getvalue(), timeout=600)
    print(out or err)
    return rc == 0


def up():
    print("[1/4] compute host ...", flush=True)
    ctl = shlex.quote(REMOTE + "/bin/wangsictl")
    rc, out, err = ssh(f"{ctl} start && {ctl} token")
    if rc != 0:
        print("cannot reach or start wangsiEDA on", HOST, "\n", err or out)
        if os.name == "nt":
            input("press Enter to close")
        return
    token = out.splitlines()[-1].strip()
    print("      " + out.splitlines()[0])
    url = f"http://127.0.0.1:{PORT}/?token={token}"
    if port_open(PORT):
        print(f"[2/4] tunnel already open on localhost:{PORT}")
        webbrowser.open(url)
        return
    print(f"[2/4] SSH tunnel localhost:{PORT} -> {HOST}:127.0.0.1:{PORT}", flush=True)
    opened = False
    while True:
        p = subprocess.Popen([SSH] + OPTS + ["-N", "-o", "ServerAliveInterval=20", "-o", "ExitOnForwardFailure=yes",
                                             "-L", f"{PORT}:127.0.0.1:{PORT}", HOST], creationflags=NOWIN)
        for _ in range(40):
            if port_open(PORT) or p.poll() is not None:
                break
            time.sleep(0.25)
        if not opened and port_open(PORT):
            print(f"[3/4] console: http://127.0.0.1:{PORT}/  (token passed once, kept in the browser session)")
            webbrowser.open(url)
            opened = True
            print("[4/4] running. Close this window to drop the tunnel; jobs keep running on the compute host.", flush=True)
        try:
            p.wait()
        except KeyboardInterrupt:
            p.terminate()
            return
        print(time.strftime("%H:%M:%S"), "tunnel dropped; reconnecting in 3 s ...", flush=True)
        time.sleep(3)


def status():
    rc, out, err = ssh(f"{shlex.quote(REMOTE + '/bin/wangsictl')} status; cd {RQ} && PYTHONPATH=. python3 -B -m wangsieda.cli jobs")
    print(out or err)


def demo():
    """Local demo: generate the synthetic design once and serve the console on this machine."""
    env = dict(os.environ, PYTHONPATH=str(HERE / "cloud"))
    args = [sys.executable, "-B", "-u", "-m", "wangsieda.demo", "--open"] + sys.argv[2:]
    try:
        subprocess.run(args, cwd=str(HERE / "cloud"), env=env)
    except KeyboardInterrupt:
        pass


def doctor():
    rc, out, err = ssh(f"{shlex.quote(REMOTE + '/bin/wangsictl')} doctor")
    print(out or err)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    if cmd in ("up", "deploy", "status", "doctor"):
        preflight()
    if cmd == "demo":
        demo()
        sys.exit(0)
    if cmd == "deploy":
        sys.exit(0 if deploy() else 1)
    elif cmd == "status":
        status()
    elif cmd == "doctor":
        doctor()
    elif cmd == "mcp":
        print(json.dumps({"mcpServers": {"wangsiEDA": {"command": "ssh", "args": [HOST, f"{REMOTE}/bin/wangsictl", "mcp"]}}}, indent=2))
    else:
        up()
