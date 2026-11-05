"""Neo4j lifecycle without docker: download, configure, start and stop a local Neo4j Community server.

  sr neo4j-start    first run: check Java 17+, download + unpack Neo4j Community into var/neo4j, write config and the
                    initial password; then start the server in the background and wait until Bolt answers
  sr neo4j-status   running? pid, ports, log tail          sr neo4j-stop    stop it
Logs: var/neo4j/server.log   Data: var/neo4j/data   Browser (optional): http://127.0.0.1:7474

Not for managed servers: when graph.neo4j_uri points at Aura or another host nothing here is used.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

VERSION = "5.26.0"      # Neo4j 5 LTS; runs on Java 17 or 21
URL_UNIX = "https://dist.neo4j.org/neo4j-community-{v}-unix.tar.gz"
URL_WIN = "https://dist.neo4j.org/neo4j-community-{v}-windows.zip"


def _paths(settings):
    root = settings.var("neo4j", mkdir=False)
    return {"root": root, "home": root / f"neo4j-community-{VERSION}", "data": root / "data", "logs": root / "logs",
            "log": root / "server.log", "pid": root / "server.pid"}


def _host_port(settings):
    u = urlparse(settings.get("graph.neo4j_uri", "bolt://localhost:7687"))
    return (u.hostname or "localhost"), (u.port or 7687)


def port_open(host: str, port: int) -> bool:
    for h in {host, "127.0.0.1"}:
        try:
            with socket.create_connection((h, port), timeout=2):
                return True
        except OSError:
            pass
    return False


JRE_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/{os}/{arch}/jre/hotspot/normal/eclipse"
# fixed GitHub release, used when the Adoptium API host is blocked
JRE_GH = ("https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.13%2B11/"
          "OpenJDK17U-jre_{arch}_{os}_hotspot_17.0.13_11.{ext}")


def _local_jre_dirs():
    from .config import REPO_ROOT
    root = REPO_ROOT / "var" / "jre"
    return sorted(root.glob("*/bin")) if root.exists() else []


def install_jre(settings, log=print) -> str | None:
    """Portable Eclipse Temurin 17 JRE into var/jre (no admin rights, no installer)."""
    import platform
    import tarfile
    osn = {"nt": "windows"}.get(os.name, "mac" if sys.platform == "darwin" else "linux")
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    root = settings.var("jre", mkdir=False)
    root.mkdir(parents=True, exist_ok=True)
    arc = root / ("jre17.zip" if osn == "windows" else "jre17.tar.gz")
    if not arc.exists():
        try:
            _download(JRE_URL.format(os=osn, arch=arch), arc, log)
        except Exception as e:
            log(f"  Adoptium API not reachable ({e}); trying GitHub")
            _download(JRE_GH.format(os=osn, arch=arch, ext="zip" if osn == "windows" else "tar.gz"), arc, log)
    log("unpacking Java 17 runtime ...")
    if arc.suffix == ".zip":
        with zipfile.ZipFile(arc) as z:
            z.extractall(root)
    else:
        with tarfile.open(arc) as t:
            t.extractall(root)
    exe, major, line = java_info()
    return exe


def java_info() -> tuple[str | None, int | None, str]:
    """(java executable, major version, raw version line). JAVA_HOME, then PATH, then the portable var/jre."""
    cands = []
    jh = os.environ.get("JAVA_HOME")
    if jh:
        cands.append(str(Path(jh) / "bin" / ("java.exe" if os.name == "nt" else "java")))
    w = shutil.which("java")
    if w:
        cands.append(w)
    for b in _local_jre_dirs():
        cands.append(str(b / ("java.exe" if os.name == "nt" else "java")))
    for exe in cands:
        if not Path(exe).exists() and not shutil.which(exe):
            continue
        try:
            out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
            txt = (out.stderr or "") + "\n" + (out.stdout or "")
            line = next((l for l in txt.splitlines() if "version" in l), "")   # skips "Picked up JAVA_TOOL_OPTIONS"
            m = re.search(r'version "(\d+)(?:\.(\d+))?', line)
            if m:
                major = int(m.group(1))
                if major == 1 and m.group(2):
                    major = int(m.group(2))
                return exe, major, line
        except Exception:
            continue
    return None, None, ""


def _download(url: str, dest: Path, log) -> None:
    tmp = dest.with_suffix(".part")
    log(f"downloading {url}\n  -> {dest} (once)")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            done += len(b)
            if time.time() - last > 5:
                last = time.time()
                log(f"  {done / 1e6:.0f} MB" + (f" / {total / 1e6:.0f} MB" if total else ""))
    tmp.replace(dest)


def password(settings, create: bool = True) -> str:
    """SR_NEO4J_PASSWORD (env / .env) > graph.neo4j_password (settings) > var/neo4j/.password.
    Nothing is hard-coded: on first use a random password is generated into var/neo4j/.password (gitignored, 0600)."""
    pw = settings.secret("SR_NEO4J_PASSWORD") or str(settings.get("graph.neo4j_password") or "")
    if pw:
        return pw
    f = settings.var("neo4j", mkdir=False) / ".password"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    if not create:
        return ""
    import secrets
    f.parent.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(18)
    f.write_text(pw + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return pw


_password = password


def _env(java: str, home: Path) -> dict:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(Path(java).resolve().parent.parent)
    env["NEO4J_HOME"] = str(home)
    env["NEO4J_CONF"] = str(home / "conf")
    return env


def _bin(home: Path, name: str) -> str:
    return str(home / "bin" / (name + (".bat" if os.name == "nt" else "")))


def setup(settings, java: str, heap: str = "1g", log=print) -> Path:
    import tarfile
    p = _paths(settings)
    p["root"].mkdir(parents=True, exist_ok=True)
    if not (p["home"] / "lib").exists():
        win = os.name == "nt"
        arc = p["root"] / (f"neo4j-{VERSION}.zip" if win else f"neo4j-{VERSION}.tar.gz")
        if not arc.exists():
            _download((URL_WIN if win else URL_UNIX).format(v=VERSION), arc, log)
        log("unpacking ...")
        if win:
            with zipfile.ZipFile(arc) as z:
                z.extractall(p["root"])
        else:
            with tarfile.open(arc) as t:
                t.extractall(p["root"])
    host, port = _host_port(settings)
    conf = p["home"] / "conf" / "neo4j.conf"
    keys = {
        "server.default_listen_address": "127.0.0.1",
        "server.bolt.listen_address": f"127.0.0.1:{port}",
        "server.http.listen_address": "127.0.0.1:7474",
        "server.https.enabled": "false",
        "server.directories.data": p["data"].as_posix(),
        "server.directories.logs": p["logs"].as_posix(),
        "server.memory.heap.initial_size": "256m",
        "server.memory.heap.max_size": heap,
        "server.memory.pagecache.size": "256m",
    }
    lines = [l for l in conf.read_text(encoding="utf-8").splitlines()
             if l.split("=", 1)[0].strip() not in keys]
    lines += ["", "# --- written by `sr neo4j-start`"] + [f"{k}={v}" for k, v in keys.items()]
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not (p["data"] / "dbms" / "auth.ini").exists():          # first start: set the password before the first boot
        pw = _password(settings)
        if len(pw) < 8:
            raise RuntimeError("Neo4j needs a password of at least 8 characters: set graph.neo4j_password or SR_NEO4J_PASSWORD")
        r = subprocess.run([_bin(p["home"], "neo4j-admin"), "dbms", "set-initial-password", pw], env=_env(java, p["home"]),
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            log("could not set the initial password: " + (r.stderr or r.stdout).strip()[-400:])
    return p["home"]


def start(settings, heap: str = "1g", wait_s: int = 180, log=print) -> int:
    host, port = _host_port(settings)
    if port_open(host, port):
        log(f"Neo4j already answering on {host}:{port}")
        return 0
    java, major, line = java_info()
    if not java or (major or 0) < 17:
        log(("Java not found" if not java else f"Java {major} is too old (Neo4j 5 needs 17+)") +
            "; downloading a portable Java 17 runtime into var/jre (~45 MB, once, no admin rights needed) ...")
        try:
            install_jre(settings, log)
        except Exception as e:
            log(f"could not download Java ({e}). Install it instead: winget install Microsoft.OpenJDK.17, open a NEW terminal, retry.")
            return 1
        java, major, line = java_info()
        if not java or (major or 0) < 17:
            log("Java 17 still not found; install it with: winget install Microsoft.OpenJDK.17")
            return 1
    log(f"Java: {line}")
    try:
        home = setup(settings, java, heap, log)
    except Exception as e:
        log(f"Neo4j setup failed: {e}")
        return 1
    p = _paths(settings)
    p["logs"].mkdir(parents=True, exist_ok=True)
    logf = open(p["log"], "ab")
    kw = {"cwd": str(home), "stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": _env(java, home)}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen([_bin(home, "neo4j"), "console"], **kw)
    p["pid"].write_text(str(proc.pid))
    log(f"starting Neo4j (pid {proc.pid}); log: {p['log']}")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            log(f"Neo4j exited with code {proc.returncode}. Last log lines:\n" + tail(settings))
            return 1
        if port_open(host, port):
            time.sleep(2)
            log(f"Neo4j is up on {host}:{port} after {time.time() - t0:.0f}s")
            return 0
        time.sleep(2)
    log(f"no answer on {host}:{port} after {wait_s}s. Last log lines:\n" + tail(settings))
    return 1


def tail(settings, n: int = 25) -> str:
    p = _paths(settings)["log"]
    if not p.exists():
        return "(no log yet)"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def stop(settings, log=print) -> int:
    p = _paths(settings)
    if not p["pid"].exists():
        log("no pid file; Neo4j not started by `sr neo4j-start`")
        return 1
    pid = int(p["pid"].read_text().strip() or 0)
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        log(f"stopped Neo4j (pid {pid})")
    except Exception as e:
        log(f"could not stop pid {pid}: {e}")
    p["pid"].unlink(missing_ok=True)
    return 0


def status(settings, log=print) -> int:
    host, port = _host_port(settings)
    up = port_open(host, port)
    p = _paths(settings)
    java, major, line = java_info()
    log(f"Neo4j {'UP' if up else 'DOWN'} on {host}:{port}")
    log(f"installed: {(p['home'] / 'lib').exists()} at {p['home']}")
    log(f"java: {line or 'not found'}")
    if p["pid"].exists():
        log(f"pid file: {p['pid'].read_text().strip()}")
    if not up:
        log("log tail:\n" + tail(settings, 15))
    return 0 if up else 1
