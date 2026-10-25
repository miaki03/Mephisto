"""附件存储：内容寻址的 blob + 分片上传区（文件侧）。

为什么按 `sha256` 命名而不是原文件名：
1. 天然去重——同一张图发两次只占一份空间；
2. 天然防路径穿越——文件名来自客户端，绝不能直接拼路径；
3. 原文件名只存数据库，展示时再取。

分片上传（见 docs/17）：单分片 4 MB、单文件 64 MB。拼装是**流式**的：
边读边写边算哈希，不把 64 MB 塞进内存。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

DIGEST_PREFIX = "sha256:"
CHUNK_SIZE = 4 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
_READ_BLOCK = 1024 * 1024


def digest_of_bytes(data: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def digest_of_file(source: Path) -> str:
    hasher = hashlib.sha256()
    with open(source, "rb") as handle:
        while True:
            block = handle.read(_READ_BLOCK)
            if not block:
                break
            hasher.update(block)
    return DIGEST_PREFIX + hasher.hexdigest()


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, digest: str) -> Path:
        if not digest.startswith(DIGEST_PREFIX):
            raise ValueError(f"digest 必须以 {DIGEST_PREFIX} 开头：{digest!r}")
        hex_part = digest[len(DIGEST_PREFIX) :]
        if len(hex_part) != 64 or any(ch not in "0123456789abcdef" for ch in hex_part):
            raise ValueError(f"digest 不是 64 位小写十六进制：{digest!r}")
        return self.root / hex_part[:2] / hex_part

    def has(self, digest: str) -> bool:
        return self.path_for(digest).exists()

    def size(self, digest: str) -> int:
        return self.path_for(digest).stat().st_size

    def put_bytes(self, data: bytes) -> str:
        digest = digest_of_bytes(data)
        target = self.path_for(digest)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return digest

    def adopt(self, source: Path) -> str:
        """把已落盘的文件收进 blob 区（流式算哈希，避免整块读入内存）。"""
        digest = self.digest_of_file(source)
        target = self.path_for(digest)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        else:
            source.unlink(missing_ok=True)
        return digest

    @staticmethod
    def digest_of_file(source: Path) -> str:
        return digest_of_file(source)


class UploadArea:
    """未完成的分片。按 `upload_id` 分目录，2 小时无进展由清理任务删除。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def dir_for(self, upload_id: str) -> Path:
        safe = "".join(ch for ch in upload_id if ch.isalnum())
        if not safe:
            raise ValueError(f"upload_id 非法：{upload_id!r}")
        return self.root / safe

    def chunk_path(self, upload_id: str, index: int) -> Path:
        if index < 0:
            raise ValueError("分片序号不能为负")
        return self.dir_for(upload_id) / f"{index:04d}.part"

    def write_chunk(self, upload_id: str, index: int, data: bytes) -> int:
        if len(data) > CHUNK_SIZE:
            raise ValueError(f"单分片 {len(data)} 字节超过上限 {CHUNK_SIZE}")
        path = self.chunk_path(upload_id, index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return len(data)

    def received(self, upload_id: str, chunk_count: int) -> list[int]:
        directory = self.dir_for(upload_id)
        if not directory.exists():
            return []
        found = []
        for index in range(chunk_count):
            if (directory / f"{index:04d}.part").exists():
                found.append(index)
        return found

    def assemble(self, upload_id: str, chunk_count: int, target: Path) -> int:
        """按序拼接到 `target`，返回总字节数。缺片直接报错，不产出半成品。"""
        missing = [i for i in range(chunk_count) if not self.chunk_path(upload_id, i).exists()]
        if missing:
            raise ValueError(f"缺少分片：{missing[:5]}{'…' if len(missing) > 5 else ''}")
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with open(target, "wb") as out:
            for index in range(chunk_count):
                block = self.chunk_path(upload_id, index).read_bytes()
                out.write(block)
                total += len(block)
        return total

    def cleanup(self, upload_id: str) -> None:
        shutil.rmtree(self.dir_for(upload_id), ignore_errors=True)
