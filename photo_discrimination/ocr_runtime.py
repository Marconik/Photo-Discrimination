"""Opt-in local OCR deployments. Importing this module never downloads anything."""

from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import venv
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
PADDLE_VERSION = "3.2.2"
OCR_VERSION = "3.3.2"
PADDLEX_VERSION = "3.3.13"
GPU_VARIANTS = {
    "cu118": "CUDA 11.8（cu118）· PaddlePaddle 3.2.2",
    "cu126": "CUDA 12.6 · PaddlePaddle 3.2.2（需要较新驱动）",
    "cu129": "CUDA 12.9 · PaddlePaddle 3.2.2（需要较新驱动）",
}
GPU_CUDA = {"cu118": "11.8", "cu126": "12.6", "cu129": "12.9"}
MODEL_VARIANTS = {
    "mobile": "PP-OCRv5 轻量版 · 中英日 · 推荐先体验",
    "server": "PP-OCRv5 高精度版 · 中英日 · 占用更多资源",
}
MODEL_BASE = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class RuntimeErrorInfo(Exception):
    pass


class Cancelled(RuntimeErrorInfo):
    pass


@dataclass(frozen=True)
class OcrChoice:
    device: str = "cpu"
    variant: str = ""
    model: str = "mobile"

    def validate(self):
        if self.device not in ("cpu", "gpu") or self.model not in MODEL_VARIANTS:
            raise RuntimeErrorInfo("请选择受支持的 OCR 工具和文字模型。")
        if self.device == "gpu" and self.variant not in GPU_VARIANTS:
            raise RuntimeErrorInfo("请手动选择 GPU 版本（CUDA 11.8 / 12.6 / 12.9）。")
        if self.device == "cpu" and self.variant:
            raise RuntimeErrorInfo("CPU 环境不使用 CUDA 版本。")

    @property
    def runtime_key(self):
        self.validate()
        return "cpu" if self.device == "cpu" else f"gpu-{self.variant}"

    @property
    def key(self):
        return f"{self.runtime_key}-{self.model}"

    @property
    def title(self):
        return f"PaddleOCR · {self.device.upper()}" + (f" · {self.variant}" if self.device == "gpu" else "")

    def model_names(self):
        return (f"PP-OCRv5_{self.model}_det", f"PP-OCRv5_{self.model}_rec")


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def runtime_python(choice: OcrChoice, root: Path = ROOT) -> Path:
    base = root / "runtimes" / choice.runtime_key
    return base / "Scripts" / "python.exe" if os.name == "nt" else base / "bin" / "python"


def marker_path(choice: OcrChoice, root: Path = ROOT):
    return root / "data" / "settings" / f"ready-{choice.key}.json"


def model_ready(folder: Path) -> bool:
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0
               for name in ("inference.json", "inference.pdiparams", "inference.yml"))


def ready_status(choice: OcrChoice, root: Path = ROOT) -> tuple[bool, str]:
    try:
        choice.validate()
        path = marker_path(choice, root)
        if not path.is_file():
            return False, "尚未部署；点击下载并部署后才会联网。"
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("paddle"), data.get("paddleocr"), data.get("paddlex")) != (PADDLE_VERSION, OCR_VERSION, PADDLEX_VERSION):
            return False, "已下载环境版本发生变化，请重新部署验证。"
        if data.get("choice") != asdict(choice):
            return False, "部署记录与当前选择不符。"
        if choice.device == "gpu" and data.get("cuda") != GPU_CUDA[choice.variant]:
            return False, "CUDA 构建记录与手动选择不符，请重新验证部署。"
        if not runtime_python(choice, root).is_file():
            return False, "运行环境缺失，请重新部署。"
        if not all(model_ready(root / "models" / name) for name in choice.model_names()):
            return False, "模型文件不完整，请重新下载。"
        return True, f"已部署并验证 · {data.get('device', choice.device)} · {choice.model}"
    except (OSError, ValueError, RuntimeErrorInfo) as exc:
        return False, str(exc)


def load_active(root: Path = ROOT) -> OcrChoice | None:
    path = root / "data" / "settings" / "ocr.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        choice = OcrChoice(**data["active"])
        choice.validate()
        return choice
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeErrorInfo(f"识别工具设置无法读取：{exc}") from exc


def save_active(choice: OcrChoice, root: Path = ROOT):
    ready, reason = ready_status(choice, root)
    if not ready:
        raise RuntimeErrorInfo(reason)
    atomic_json(root / "data" / "settings" / "ocr.json", {"version": 1, "active": asdict(choice)})


def worker_env(root: Path, offline: bool = False):
    environment = os.environ.copy()
    environment.update({
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "PADDLE_PDX_CACHE_HOME": str(root / "cache" / "paddlex"),
        "PADDLE_HOME": str(root / "cache" / "paddle"),
        "HF_HOME": str(root / "cache" / "huggingface"),
        "PIP_CACHE_DIR": str(root / "cache" / "pip"),
    })
    if offline:
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
    return environment


def hardware_info() -> str:
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=CREATE_FLAGS)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "未读取到 NVIDIA 显卡；CPU 模式仍可使用。"


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled("已取消；已下载文件保留，下次部署会复用完整文件。")


def _stream_process(command, root: Path, log: Callable[[str], None], cancel=None, timeout=1800, offline=False):
    """No shell; drain merged output on a reader thread to allow cancellation."""
    check_cancel(cancel)
    process = subprocess.Popen([str(part) for part in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=worker_env(root, offline), cwd=root, creationflags=CREATE_FLAGS)
    lines = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    start = time.monotonic()
    tail = []
    try:
        while True:
            check_cancel(cancel)
            if time.monotonic() - start > timeout:
                raise RuntimeErrorInfo("部署步骤超时，请检查网络后重试。")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            tail.append(line)
            tail = tail[-16:]
            log(line)
        code = process.wait(timeout=15)
        if code:
            raise RuntimeErrorInfo("部署命令失败：\n" + "\n".join(tail))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        process.stdout.close()
        thread.join(timeout=2)


def download_model(name: str, root: Path, log, cancel=None):
    target = root / "models" / name
    if model_ready(target):
        log(f"复用已下载模型：{name}")
        return
    archive = root / "cache" / "downloads" / f"{name}_infer.tar"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        partial = archive.with_suffix(".part")
        log(f"下载模型：{name}")
        request = urllib.request.Request(MODEL_BASE + f"{name}_infer.tar", headers={"User-Agent": "Photo-Discrimination/0.2"})
        with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as stream:
            total = int(response.headers.get("Content-Length", 0))
            size, previous = 0, time.monotonic()
            while True:
                check_cancel(cancel)
                block = response.read(256 * 1024)
                if not block:
                    break
                stream.write(block)
                size += len(block)
                if time.monotonic() - previous >= 0.5:
                    log(f"{name}：{size / 1048576:.1f} MB" + (f" / {total / 1048576:.1f} MB" if total else ""))
                    previous = time.monotonic()
            if total and size != total:
                raise RuntimeErrorInfo(f"{name} 下载不完整，请重试。")
        os.replace(partial, archive)
    # Extract only the three inference files; never honor archive paths or links.
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            for required in ("inference.json", "inference.pdiparams", "inference.yml"):
                check_cancel(cancel)
                options = [member for member in members if member.isfile() and PurePosixPath(member.name).name == required]
                if len(options) != 1:
                    raise RuntimeErrorInfo(f"模型包缺少或重复文件：{required}")
                member = options[0]
                incoming = tar.extractfile(member)
                if incoming is None:
                    raise RuntimeErrorInfo("模型文件无法解压。")
                temporary = target / (required + ".part")
                with incoming, temporary.open("wb") as outgoing:
                    while block := incoming.read(256 * 1024):
                        check_cancel(cancel)
                        outgoing.write(block)
                if temporary.stat().st_size != member.size:
                    raise RuntimeErrorInfo("模型解压不完整。")
                os.replace(temporary, target / required)
    except (tarfile.TarError, RuntimeErrorInfo):
        # An invalid archive must not be endlessly reused on retry.
        archive.unlink(missing_ok=True)
        raise
    log(f"模型已准备：{name}")


def probe(choice: OcrChoice, root: Path = ROOT, log=lambda line: None, cancel=None):
    python = runtime_python(choice, root)
    command = [python, ROOT / "photo_discrimination" / "ocr_worker.py", "--root", root, "--choice", json.dumps(asdict(choice)), "--probe"]
    # Worker emits one machine-readable marker amidst third-party library logs.
    result = {}
    def receive(line):
        if line.startswith("PD_EVENT:"):
            event = json.loads(line[len("PD_EVENT:"):])
            if event.get("type") == "probe":
                result.update(event)
        log(line)
    _stream_process(command, root, receive, cancel, timeout=240, offline=True)
    if not result.get("ok"):
        raise RuntimeErrorInfo("模型初始化或实际推理验证失败，未标记为可用。")
    expected_device = "gpu:0" if choice.device == "gpu" else "cpu"
    if result.get("device") != expected_device:
        raise RuntimeErrorInfo("验证使用的设备与选择不一致；不会启用自动降级的环境。")
    if choice.device == "gpu" and result.get("cuda") != GPU_CUDA[choice.variant]:
        raise RuntimeErrorInfo("实际 CUDA 运行版本与手动选择不符，请重新部署所选构建。")
    if (result.get("paddle"), result.get("paddleocr"), result.get("paddlex")) != (PADDLE_VERSION, OCR_VERSION, PADDLEX_VERSION):
        raise RuntimeErrorInfo("运行依赖版本与当前配置不一致，请重新部署。")
    return result


def deploy(choice: OcrChoice, root: Path = ROOT, log=lambda line: None, cancel=None, reuse_packages=False):
    choice.validate()
    if sys.version_info[:2] not in ((3, 10), (3, 11), (3, 12), (3, 13)) or platform.architecture()[0] != "64bit":
        raise RuntimeErrorInfo("请使用 64 位 Python 3.10–3.13 启动程序后部署。")
    if os.name != "nt":
        raise RuntimeErrorInfo("当前下载配置面向 Windows x64。")
    root = Path(root).resolve()
    (root / "data" / "settings").mkdir(parents=True, exist_ok=True)
    lock = root / "data" / "settings" / "deployment.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeErrorInfo("已有部署在进行；如上次异常退出，请确认没有部署进程，再移除 data/settings/deployment.lock。") from exc
    os.close(descriptor)
    try:
        marker_path(choice, root).unlink(missing_ok=True)
        check_cancel(cancel)
        python = runtime_python(choice, root)
        if not python.exists():
            log("创建独立 OCR 环境，不修改桌面程序或系统 Python…")
            venv.EnvBuilder(with_pip=True).create(python.parent.parent)
        if not reuse_packages:
            for old_marker in (root / "data" / "settings").glob(f"ready-{choice.runtime_key}-*.json"):
                old_marker.unlink()
            log(f"安装 PaddlePaddle {PADDLE_VERSION}（首次下载 GPU 环境可能数百 MB 至数 GB）…")
            package = "paddlepaddle" if choice.device == "cpu" else "paddlepaddle-gpu"
            index = "cpu" if choice.device == "cpu" else choice.variant
            common = [python, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--timeout", "90", "--retries", "3", "--constraint", ROOT / "requirements-ocr-constraints.txt"]
            # Obtain the selected CUDA build ONLY from its official index. Resolve
            # dependencies separately on PyPI, whose newer constrained versions
            # may not exist on Paddle's curated mirror.
            _stream_process([*common, f"{package}=={PADDLE_VERSION}", "--no-deps", "--index-url", f"https://www.paddlepaddle.org.cn/packages/stable/{index}/"], root, log, cancel)
            log("安装 OCR 识别依赖…")
            _stream_process([*common, f"{package}=={PADDLE_VERSION}", f"paddleocr=={OCR_VERSION}", f"paddlex[ocr-core]=={PADDLEX_VERSION}", "numpy<2.3", "--index-url", "https://pypi.org/simple"], root, log, cancel)
        for name in choice.model_names():
            download_model(name, root, log, cancel)
        log("验证本地模型与实际设备推理；不会自动降级到 CPU…")
        result = probe(choice, root, log, cancel)
        atomic_json(marker_path(choice, root), {"choice": asdict(choice), "paddle": PADDLE_VERSION, "paddleocr": OCR_VERSION, "paddlex": PADDLEX_VERSION, "device": result["device"], "cuda": result.get("cuda"), "verified_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        log("下载部署完成；可点击“使用此配置”启用。")
        return choice
    finally:
        lock.unlink(missing_ok=True)
