"""JanusGraph lifecycle without docker and without the distribution's launcher scripts.

The Windows launcher (gremlin-server.bat) breaks on long paths ("input line is too long"), needs HADOOP_HOME /
winutils.exe and ties up a console; so the server is started here directly with `java`, a wildcard classpath
and the options the distribution's own scripts use. Works the same on Windows, Linux and macOS.

  sr janus-start    first run: check Java, download + unpack JanusGraph into var/janusgraph, write config;
                    then start the server in the background, wait until it answers, create the indexes
  sr janus-status   running? pid, port, log tail          sr janus-stop    stop it
Logs: var/janusgraph/server.log   Data: var/janusgraph/data (BerkeleyDB-JE + Lucene)
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

VERSION = "1.1.0"
URL = "https://github.com/JanusGraph/janusgraph/releases/download/v{v}/janusgraph-{v}.zip"


def _paths(settings):
    root = settings.var("janusgraph", mkdir=False)
    return {"root": root, "home": root / f"janusgraph-{VERSION}", "zip": root / f"janusgraph-{VERSION}.zip",
            "data": root / "data", "log": root / "server.log", "pid": root / "server.pid"}


def _host_port(settings):
    from urllib.parse import urlparse
    u = urlparse(settings.get("graph.gremlin_url", "ws://localhost:8182/gremlin"))
    return (u.hostname or "localhost"), (u.port or 8182)


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


def setup(settings, log=print) -> Path:
    p = _paths(settings)
    p["root"].mkdir(parents=True, exist_ok=True)
    if not (p["home"] / "lib").exists():
        if not p["zip"].exists():
            _download(URL.format(v=VERSION), p["zip"], log)
        log("unpacking ...")
        with zipfile.ZipFile(p["zip"]) as z:
            z.extractall(p["root"])
    data = p["data"].as_posix()
    (p["home"] / "conf" / "css-berkeleyje-lucene.properties").write_text(
        "gremlin.graph=org.janusgraph.core.JanusGraphFactory\n"
        "storage.backend=berkeleyje\n"
        f"storage.directory={data}/berkeley\n"
        "index.search.backend=lucene\n"
        f"index.search.directory={data}/lucene\n"
        "schema.default=default\n", encoding="ascii")
    host, port = _host_port(settings)
    src = (p["home"] / "conf" / "gremlin-server" / "gremlin-server.yaml").read_text(encoding="utf-8")
    src = src.replace("graph: conf/janusgraph-inmemory.properties", "graph: conf/css-berkeleyje-lucene.properties")
    src = re.sub(r"(?m)^host: .*$", "host: 127.0.0.1", src)
    src = re.sub(r"(?m)^port: .*$", f"port: {port}", src)
    (p["home"] / "conf" / "gremlin-server" / "gremlin-server-css.yaml").write_text(src, encoding="utf-8")
    return p["home"]


def _cmd(java: str, home: Path, heap: str) -> list[str]:
    lib, ext, conf = home / "lib", home / "ext", home / "conf"
    sep = ";" if os.name == "nt" else ":"
    jamm = next(iter(sorted(lib.glob("jamm-*.jar"))), None)
    cmd = [java, "-Xms256m", f"-Xmx{heap}", "-XX:+UseG1GC", "-Xss256k",
           f"-Dlog4j2.configurationFile={(conf / 'log4j2-server.xml').as_uri()}",
           f"-Djanusgraph.logdir={home / 'logs'}", f"-Dtinkerpop.ext={ext}", f"-Dhadoop.home.dir={home}"]
    if jamm:
        cmd.append(f"-javaagent:{jamm}")
    cmd += ["-cp", f"{lib / '*'}{sep}{ext / '*'}", "org.janusgraph.graphdb.server.JanusGraphServer",
            str(conf / "gremlin-server" / "gremlin-server-css.yaml")]
    return cmd


def start(settings, heap: str = "1g", wait_s: int = 180, log=print) -> int:
    host, port = _host_port(settings)
    if port_open(host, port):
        log(f"JanusGraph already answering on {host}:{port}")
        return 0
    java, major, line = java_info()
    if not java or (major or 0) < 11:
        log(("Java not found" if not java else f"Java {major} is too old") + "; downloading a portable Java 17 runtime into var/jre "
            "(~45 MB, once, no admin rights needed) ...")
        try:
            install_jre(settings, log)
        except Exception as e:
            log(f"could not download Java ({e}). Install it instead: winget install Microsoft.OpenJDK.17, open a NEW terminal, retry.")
            return 1
        java, major, line = java_info()
        if not java or (major or 0) < 11:
            log("Java 17 still not found; install it with: winget install Microsoft.OpenJDK.17")
            return 1
    log(f"Java: {line}")
    home = setup(settings, log)
    p = _paths(settings)
    (home / "logs").mkdir(exist_ok=True)
    logf = open(p["log"], "ab")
    kw = {"cwd": str(home), "stdout": logf, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(_cmd(java, home, heap), **kw)
    p["pid"].write_text(str(proc.pid))
    log(f"starting JanusGraph (pid {proc.pid}); log: {p['log']}")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            log(f"JanusGraph exited with code {proc.returncode}. Last log lines:\n" + tail(settings))
            return 1
        if port_open(host, port):
            time.sleep(2)
            log(f"JanusGraph is up on {host}:{port} after {time.time() - t0:.0f}s")
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
        log("no pid file; JanusGraph not started by `sr janus-start`")
        return 1
    pid = int(p["pid"].read_text().strip() or 0)
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        log(f"stopped JanusGraph (pid {pid})")
    except Exception as e:
        log(f"could not stop pid {pid}: {e}")
    p["pid"].unlink(missing_ok=True)
    return 0


def status(settings, log=print) -> int:
    host, port = _host_port(settings)
    up = port_open(host, port)
    p = _paths(settings)
    java, major, line = java_info()
    log(f"JanusGraph {'UP' if up else 'DOWN'} on {host}:{port}")
    log(f"installed: {(p['home'] / 'lib').exists()} at {p['home']}")
    log(f"java: {line or 'not found'}")
    if p["pid"].exists():
        log(f"pid file: {p['pid'].read_text().strip()}")
    if not up:
        log("log tail:\n" + tail(settings, 15))
    return 0 if up else 1
