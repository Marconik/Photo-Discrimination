"""Album and sidecar storage. No GUI or OCR runtime dependencies."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
INDEX_NAME = "OCR.json"


class AlbumError(Exception):
    """A user-actionable album error."""


def compact_text(text: str) -> str:
    """Remove all Unicode whitespace, retaining the recognized characters."""
    return "".join(text.split())


def search_text(text: str) -> str:
    return compact_text(unicodedata.normalize("NFKC", text).casefold())


def keywords(query: str) -> tuple[str, ...]:
    # Normalize width before splitting, so full-width spaces work too.
    return tuple(dict.fromkeys(search_text(part) for part in
                               unicodedata.normalize("NFKC", query).split()))


def matches(text: str, terms: Iterable[str]) -> bool:
    haystack = search_text(text)
    return all(term in haystack for term in terms)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复的照片索引：{key}")
        result[key] = value
    return result


def _read_index(path: Path) -> tuple[dict[str, str], bytes | None]:
    if path.is_symlink():
        raise AlbumError("OCR.json 不能是符号链接。请使用相册内的普通文件。")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        raise AlbumError(f"无法读取 {path.name}：{exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in data.items()
        ):
            raise ValueError('格式应为 {"照片名.jpg": "识别文字"}')
        return {key: compact_text(value) for key, value in data.items()}, raw
    except (ValueError, UnicodeError) as exc:
        raise AlbumError(f"{path.name} 格式不正确，原文件未修改。\n{exc}") from exc


@dataclass
class JsonIndex:
    path: Path
    texts: dict[str, str]
    original: bytes | None

    @classmethod
    def open(cls, folder: Path) -> JsonIndex:
        path = folder / INDEX_NAME
        data, original = _read_index(path)
        return cls(path, data, original)

    def write(self, data: dict[str, str]) -> None:
        """Replace a complete JSON document atomically, preserving old data on failure."""
        normalized = {name: compact_text(value) for name, value in data.items()}
        payload = (json.dumps(normalized, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            _, current = _read_index(self.path)
            if current != self.original:
                raise AlbumError("OCR.json 已被其他程序修改，请重新载入文件夹后再保存。")
            fd, temporary = tempfile.mkstemp(prefix=".OCR-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Check again after writing the temporary file.
                _, current = _read_index(self.path)
                if current != self.original:
                    raise AlbumError("OCR.json 已被其他程序修改，请重新载入后再保存。")
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError as exc:
            raise AlbumError(f"无法保存 OCR.json：{exc}") from exc
        self.texts = normalized
        self.original = payload

    def set_text(self, filename: str, text: str) -> None:
        updated = dict(self.texts)
        updated[filename] = compact_text(text)
        self.write(updated)


@dataclass(frozen=True)
class Photo:
    name: str
    size: int
    mtime_ns: int


@dataclass
class Album:
    root: Path
    photos: list[Photo]
    index: JsonIndex
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def open(cls, folder: str | Path) -> Album:
        root = Path(folder).resolve(strict=True)
        if not root.is_dir():
            raise AlbumError("请选择一个文件夹。")
        # Load index first. A malformed document must never be silently overwritten.
        index = JsonIndex.open(root)
        photos, warnings = [], []
        try:
            for path in root.iterdir():
                if path.suffix.lower() not in IMAGE_SUFFIXES or path.is_symlink():
                    continue
                try:
                    if not path.is_file():
                        continue
                    info = path.stat()
                    photos.append(Photo(path.name, info.st_size, info.st_mtime_ns))
                except OSError as exc:
                    warnings.append(f"{path.name}：{exc}")
        except OSError as exc:
            raise AlbumError(f"无法扫描文件夹：{exc}") from exc
        photos.sort(key=lambda photo: (photo.name.casefold(), photo.name))
        # Opening an existing album is read-only. First save creates OCR.json.
        return cls(root, photos, index, warnings)

    def path(self, name: str) -> Path:
        if name not in {photo.name for photo in self.photos} or Path(name).name != name:
            raise AlbumError("照片不在当前相册中。")
        path = self.root / name
        if path.is_symlink():
            raise AlbumError("不支持操作符号链接照片。")
        return path


@dataclass
class MoveReport:
    destination: Path
    moved: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def move_selected(
    album: Album,
    names: list[str],
    destination: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> MoveReport:
    """Move into a NEW directory, persisting destination text before source removal.

    Copy+unlink supports different volumes. A crash can leave a duplicate photo,
    but never intentionally deletes the only copy before its OCR index is durable.
    An error stops the remaining batch; completed moves remain completed.
    """
    names = list(dict.fromkeys(names))
    if not names:
        raise AlbumError("请先勾选照片。")
    for name in names:
        source = album.path(name)
        if not source.is_file():
            raise AlbumError(f"照片已不存在：{name}。请刷新相册。")
    destination = Path(os.path.abspath(destination))
    if destination.exists() or destination.is_symlink():
        raise AlbumError("导出文件夹已存在，请输入一个新的文件夹名称；不会覆盖已有文件。")
    if not destination.parent.is_dir():
        raise AlbumError("导出位置不存在。")
    # Establish source writability and detect conflicting edits before copying.
    album.index.write(album.index.texts)
    destination.mkdir(exist_ok=False)
    target_index = JsonIndex.open(destination)
    report = MoveReport(destination)
    try:
        target_index.write({})
    except Exception:
        # Empty directory belongs to this operation; no photo has been moved.
        destination.rmdir()
        raise
    for position, name in enumerate(names, 1):
        source, target = album.path(name), destination / name
        copied = False
        target_created = False
        removed = False
        try:
            before = source.stat()
            with source.open("rb") as incoming:
                with target.open("xb") as outgoing:
                    target_created = True
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            copied = True
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise AlbumError("复制时原图发生变化，已保留原图，请刷新后重试。")
            if target.stat().st_size != before.st_size:
                raise AlbumError("导出文件大小不完整，已保留原图。")
            shutil.copystat(source, target)
            target_texts = dict(target_index.texts)
            if name in album.index.texts:
                target_texts[name] = album.index.texts[name]
            target_index.write(target_texts)
            # Do not remove source if someone changed its index while copying.
            _, current = _read_index(album.index.path)
            if current != album.index.original:
                raise AlbumError("源 OCR.json 已被修改，已保留原图和导出副本，请刷新。")
            final_stat = source.stat()
            if (before.st_size, before.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
                raise AlbumError("原图发生变化，已保留原图和导出副本，请刷新。")
            source.unlink()
            removed = True
            report.moved.append(name)
            source_texts = dict(album.index.texts)
            source_texts.pop(name, None)
            album.index.write(source_texts)
        except Exception as exc:
            # Incomplete target is our exclusively-created file. Keep a complete
            # target when its text was already committed (safe duplicate on failure).
            if target_created and not copied and target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            suffix = "原图已移动，目标 OCR.json 已保存；请刷新源索引。" if removed else "原图保留；请检查导出文件夹后重试。"
            report.errors.append(f"{name}：{exc}\n{suffix}")
            break
        finally:
            if progress:
                progress(position, len(names), name)
    return report
