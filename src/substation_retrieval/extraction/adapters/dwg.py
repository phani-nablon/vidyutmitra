"""DWG -> DXF conversion (no docker): ODA File Converter or LibreDWG `dwg2dxf` on PATH.

Set SR_ODA_CONVERTER to the ODAFileConverter executable if it is not on PATH."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def dwg_to_dxf(path: str | Path, work_dir: str | Path) -> Path:
    path = Path(path)
    out_dir = Path(work_dir) / ("dwg_" + path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    oda = os.environ.get("SR_ODA_CONVERTER") or shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if oda:
        subprocess.run([oda, str(path.parent), str(out_dir), "ACAD2018", "DXF", "0", "1", path.name], check=True, timeout=300)
        out = out_dir / (path.stem + ".dxf")
        if out.exists():
            return out
    lib = shutil.which("dwg2dxf")
    if lib:
        out = out_dir / (path.stem + ".dxf")
        subprocess.run([lib, "-o", str(out), str(path)], check=True, timeout=300)
        if out.exists():
            return out
    raise RuntimeError("DWG needs ODA File Converter (SR_ODA_CONVERTER) or LibreDWG dwg2dxf on PATH")
