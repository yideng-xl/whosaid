"""音频 IO：ffprobe 拿时长、ffmpeg 切块/切样本。隔离外部进程调用，便于上层纯逻辑单测。"""
from __future__ import annotations

import os
import subprocess
import tempfile


def probe_duration(path: str) -> float:
    """用 ffprobe 拿音频总秒数。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def extract_wav(src: str, start: float, dur: float) -> str:
    """切 [start, start+dur) 段为 16k 单声道临时 wav，返回路径（调用方负责删除）。"""
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run(
        ["ffmpeg", "-ss", f"{start}", "-t", f"{dur}", "-i", src,
         "-ar", "16000", "-ac", "1", tmp, "-y", "-loglevel", "error"],
        check=True,
    )
    return tmp


def extract_sample(src: str, start: float, dur: float) -> bytes:
    """切一小段转 mp3，返回字节（webview 试听用）。临时文件转完即删。"""
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-ss", f"{start}", "-t", f"{dur}", "-i", src,
             "-ac", "1", tmp, "-y", "-loglevel", "error"],
            check=True,
        )
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp)
