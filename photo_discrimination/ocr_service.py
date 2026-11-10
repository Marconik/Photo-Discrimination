"""Batch OCR process coordination, with per-image sidecar commits."""

import json
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .album import Album
from .ocr_runtime import ROOT, Cancelled, OcrChoice, RuntimeErrorInfo, _stream_process, ready_status, runtime_python


@dataclass
class OcrReport:
    saved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    device: str = ""


def recognize_album(album: Album, names: list[str], choice: OcrChoice,
                    root: Path = ROOT, progress=lambda a, b, c: None,
                    cancel: threading.Event | None = None) -> OcrReport:
    ready, reason = ready_status(choice, root)
    if not ready:
        raise RuntimeErrorInfo(reason)
    names = list(dict.fromkeys(names))
    if not names:
        raise RuntimeErrorInfo("请先勾选需要识别的照片。")
    snapshots, photos = {}, []
    for name in names:
        path = album.path(name)
        stat = path.stat()
        snapshots[name] = (stat.st_size, stat.st_mtime_ns)
        photos.append({"name": name, "path": str(path)})
    tasks = root / "cache" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    descriptor, request = tempfile.mkstemp(suffix=".json", dir=tasks)
    import os
    os.close(descriptor)
    request = Path(request)
    request.write_text(json.dumps({"photos": photos}, ensure_ascii=False), encoding="utf-8")
    report = OcrReport()
    seen = set()

    def receive(line):
        if not line.startswith("PD_EVENT:"):
            return
        event = json.loads(line[len("PD_EVENT:"):])
        kind = event.get("type")
        if kind == "ready":
            report.device = event["device"]
            progress(0, len(names), f"模型已就绪 · {report.device}")
        elif kind in ("photo", "photo_error"):
            name = event.get("name")
            if name not in snapshots or name in seen:
                raise RuntimeErrorInfo("OCR 子进程返回了未知或重复图片，任务已停止。")
            seen.add(name)
            if kind == "photo":
                stat = album.path(name).stat()
                if (stat.st_size, stat.st_mtime_ns) != snapshots[name]:
                    report.errors.append(f"{name}：识别期间原图发生变化，旧结果保留。")
                else:
                    album.index.set_text(name, event["text"])
                    report.saved.append(name)
            else:
                report.errors.append(f"{name}：{event['error']}（旧结果保留）")
            progress(len(seen), len(names), name)

    try:
        _stream_process([runtime_python(choice, root), ROOT / "photo_discrimination" / "ocr_worker.py",
                         "--root", root, "--choice", json.dumps(asdict(choice)), "--request", request],
                        root, receive, cancel, timeout=max(300, len(names) * 120), offline=True)
        if len(seen) != len(names):
            raise RuntimeErrorInfo("OCR 子进程提前退出；已保存结果保留，请重试剩余图片。")
    except Cancelled:
        report.cancelled = True
    finally:
        request.unlink(missing_ok=True)
    return report
