#!/usr/bin/env python3
"""微信语音转写助手（Windows 版）。

工作流（AI 在 SKILL.md Step 1.5 调用）:
    1. 调 wechat_windows.py voice-extract 解码语音为 WAV
    2. 调本脚本 transcribe_voices.py 批量转写所有 WAV
    3. 脚本输出 JSON: {local_id: {time, duration, text}}

转写引擎: openai-whisper（本地，base 模型，139MB），使用 Python API
缓存策略: 已转写过的 wav 跳过（基于文件 mtime + size 签名）

用法:
    transcribe_voices.py \\
        --wav-dir C:/Users/xxx/AppData/Local/Temp/voice_test/ \\
        --out /tmp/voices_<群名>_<日期>.json \\
        --model base

参数:
    --wav-dir      解码后 wav 文件所在目录
    --filter       文件名子串过滤（可选）
    --out          输出 JSON 路径
    --model        whisper 模型名（默认 base，可选 tiny / small / medium）
    --language     语言（默认 zh）
    --min-duration 最短时长秒数，短于此跳过（默认 3）
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys
import wave
from pathlib import Path

import numpy as np

CACHE_PATH = Path.home() / ".claude/skills/group-daily/voice_cache.json"


def load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def wav_duration(path):
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


def file_sig(path):
    """文件签名：mtime + size，足够区分是否变化"""
    st = path.stat()
    return f"{int(st.st_mtime)}-{st.st_size}"


def load_wav_as_audio(wav_path):
    """加载 WAV 文件为 whisper 可用的 numpy array（跨平台，不依赖 ffmpeg）。"""
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # 用 struct 解析 PCM（不需要 scipy/soundfile）
    fmt = {1: "b", 2: "h", 4: "i"}.get(sampwidth, "h")
    audio = np.array(struct.unpack(f"<{n_frames * n_channels}{fmt}", raw), dtype=np.float32)

    # 转为 mono
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    # 归一化到 [-1, 1]
    max_val = float(2 ** (8 * sampwidth - 1))
    audio = audio / max_val

    # Whisper 期望 16kHz；重采样（简易线性重采样，能处理大部分情况）
    if framerate != 16000:
        target_len = int(len(audio) * 16000 / framerate)
        indices = np.linspace(0, len(audio) - 1, target_len)
        audio = np.interp(indices, np.arange(len(audio)), audio)

    return audio.astype(np.float32)


def parse_filename(name):
    """从文件名解析 local_id。

    支持两种格式:
      1. voice_{local_id}_{msg_svr_id}.wav  (wechat_windows.py 导出)
      2. <chatroom>_<YYYYMMDD>_<HHMMSS>_<local_id>.wav (vchat/MCP 导出)
    """
    # 格式 1: voice_33830_1447208351709438244.wav
    m = re.match(r"voice_(\d+)_(\d+)\.wav$", name)
    if m:
        return {"local_id": int(m.group(1)), "msg_svr_id": int(m.group(2))}
    # 格式 2: 12345678901@chatroom_20260512_002749_11239.wav
    m = re.match(r"(.+?)_(\d{8})_(\d{6})_(\d+)\.wav$", name)
    if m:
        chatroom, date, time, local_id = m.groups()
        return {
            "chatroom": chatroom,
            "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
            "time": f"{time[:2]}:{time[2:4]}:{time[4:]}",
            "local_id": int(local_id),
        }
    return None


def transcribe_python(wav_path, model="base", language="zh"):
    """用 whisper Python API 转写（跨平台，不依赖 ffmpeg/CLI）。"""
    import whisper
    try:
        audio = load_wav_as_audio(wav_path)
        wmodel = whisper.load_model(model)
        result = wmodel.transcribe(audio, language=language, fp16=False,
                                   verbose=False)
        return result["text"].strip(), None
    except Exception as e:
        return None, f"whisper Python API 失败: {e}"


def transcribe(wav_path, model="base", language="zh"):
    """转写 WAV 文件。优先用 whisper CLI，回退到 Python API。"""
    import subprocess
    import tempfile
    import shutil

    # 优先尝试 whisper CLI（如果安装了且 ffmpeg 可用）
    if shutil.which("whisper"):
        out_dir = Path(tempfile.gettempdir()) / "whisper_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "whisper", str(wav_path),
            "--model", model,
            "--language", language,
            "--output_format", "txt",
            "--output_dir", str(out_dir),
            "--verbose", "False",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            txt_path = out_dir / f"{wav_path.stem}.txt"
            if txt_path.exists():
                text = txt_path.read_text(encoding="utf-8").strip()
                if text:
                    return text, None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError):
            pass

    # 回退：Python API（不依赖 ffmpeg）
    return transcribe_python(wav_path, model=model, language=language)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav-dir", required=True,
                    help="解码后 wav 文件所在目录")
    ap.add_argument("--filter", default="",
                    help="文件名子串过滤")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--model", default="base",
                    help="whisper 模型名（tiny/base/small/medium，默认 base）")
    ap.add_argument("--language", default="zh", help="语言（默认 zh）")
    ap.add_argument("--min-duration", type=float, default=3.0,
                    help="最短时长秒数，短于此跳过（默认 3）")
    ap.add_argument("--max-files", type=int, default=200,
                    help="最多处理多少个文件（默认 200）")
    ap.add_argument("--metadata", default="",
                    help="voice-extract JSON 元数据文件（可选，用于补充时间信息）")
    args = ap.parse_args()

    wav_dir = Path(os.path.expanduser(args.wav_dir))
    if not wav_dir.exists():
        sys.exit(f"目录不存在: {wav_dir}")

    # 加载元数据（voice-extract JSON 输出）
    metadata = {}
    if args.metadata:
        meta_path = Path(os.path.expanduser(args.metadata))
        if meta_path.exists():
            for entry in json.loads(meta_path.read_text(encoding="utf-8")):
                metadata[entry["local_id"]] = entry

    cache = load_cache()
    cache_key_prefix = f"{args.model}|{args.language}|"

    results = {}
    skipped_short = 0
    cached_hits = 0
    new_transcribed = 0
    whisper_model = None  # lazy load

    files = sorted(p for p in wav_dir.glob("*.wav")
                   if args.filter in p.name)[:args.max_files]
    print(f"扫到 {len(files)} 个候选 wav 文件", file=sys.stderr)

    for wav in files:
        info = parse_filename(wav.name)
        if not info:
            print(f"  ✗ 跳过（文件名格式不识别）: {wav.name}", file=sys.stderr)
            continue

        dur = wav_duration(wav)
        if dur is not None and dur < args.min_duration:
            skipped_short += 1
            print(f"  ⏭ 跳过（{dur:.1f}s < {args.min_duration}s）: {wav.name}",
                  file=sys.stderr)
            continue

        # 时间：优先从元数据取，其次从文件名解析
        lid = info["local_id"]
        if lid in metadata:
            time_str = metadata[lid].get("time", "")
        elif "date" in info and "time" in info:
            time_str = f"{info['date']} {info['time']}"
        else:
            time_str = ""

        cache_key = cache_key_prefix + file_sig(wav)
        if cache_key in cache:
            cached_hits += 1
            text = cache[cache_key]
            print(f"  ✓ 缓存命中: local_id={lid} {dur:.1f}s",
                  file=sys.stderr)
        else:
            print(f"  ▶ 转写中: local_id={lid} {dur:.1f}s ...",
                  file=sys.stderr)
            text, err = transcribe(wav, model=args.model, language=args.language)
            if text is None:
                print(f"    ✗ 失败: {err}", file=sys.stderr)
                continue
            cache[cache_key] = text
            new_transcribed += 1

        results[str(lid)] = {
            "time": time_str,
            "duration_s": round(dur or 0, 1),
            "text": text,
        }

    save_cache(cache)
    Path(os.path.expanduser(args.out)).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ 完成", file=sys.stderr)
    print(f"   转写 {len(results)} 条（新 {new_transcribed} + 缓存 {cached_hits}）",
          file=sys.stderr)
    print(f"   跳过过短 {skipped_short} 条", file=sys.stderr)
    print(f"   输出: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
