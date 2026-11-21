"""WhisperX 说话人分离转写 CLI：由工作台服务端以子进程方式调用，向 stdout 输出 JSON。

流程：faster-whisper 转写（与 transcribe.py 相同的简体 prompt / 术语表 / VAD）
→ wav2vec2 词级对齐 → pyannote 说话人分离 → 按时间重叠把发言段落归属到说话人。

依赖 whisperx + torch（CPU 版），安装在项目 asr_venv 中；
pyannote 模型需要 Hugging Face Token（服务端经 --token 传入）并在 HF 上接受模型协议。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe import fmt_ts, group_paragraphs  # noqa: E402


def speaker_label_map(speakers: list[str]) -> dict[str, str]:
    """把 pyannote 的 SPEAKER_00 按出场顺序映射为 发言人A/B/…。"""
    mapping: dict[str, str] = {}
    for spk in speakers:
        if spk not in mapping:
            mapping[spk] = f"发言人{chr(ord('A') + len(mapping))}"
    return mapping


def _report_progress(progress_path: str | None, percent: int, note: str = "") -> None:
    if not progress_path:
        return
    try:
        Path(progress_path).write_text(f"{percent}|{note}", encoding="utf-8")
    except OSError:
        pass


def _swallow_stdout(func, progress_path: str | None, base: int, span: int):
    """包住第三方调用：吞掉其 stdout 打印（防止污染 JSON 输出），
    并把其中的 'Progress: xx%' 换算为整体进度写入进度文件。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        value = func()
    if progress_path:
        match = re.findall(r"Progress: ([\d.]+)%", buffer.getvalue())
        if match:
            pct = base + int(float(match[-1]) * span / 100)
            _report_progress(progress_path, pct)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--glossary-file")
    parser.add_argument("--token", required=True)
    parser.add_argument("--progress-file")
    args = parser.parse_args()
    _report_progress(args.progress_file, 2, "加载模型")

    started = time.time()
    import whisperx
    # 用 faster-whisper 自带的 PyAV 解码（asr_venv 内 av 12.3.0），
    # 不依赖系统 ffmpeg 可执行文件
    from faster_whisper.audio import decode_audio
    audio = decode_audio(args.audio)

    prompt_parts = ["以下是中文会议录音的转写，请一律使用简体中文（白话文）输出，不要使用繁体字。"]
    if args.glossary_file and Path(args.glossary_file).exists():
        glossary = Path(args.glossary_file).read_text(encoding="utf-8").strip()
        if glossary:
            prompt_parts.append(
                "以下是本项目常用专有名词、缩写与人名，转写时遇到相近发音优先采用这些写法：\n"
                + glossary)
    initial_prompt = "\n".join(prompt_parts)

    device = "cpu"
    compute_type = "int8"
    asr_options = {"initial_prompt": initial_prompt, "condition_on_previous_text": False}
    model = _swallow_stdout(
        lambda: whisperx.load_model(args.model, device, compute_type=compute_type,
                                    language=args.language or None, asr_options=asr_options,
                                    threads=max(1, (os.cpu_count() or 4) // 2)),
        args.progress_file, 2, 3)
    _report_progress(args.progress_file, 5, "语音转写")
    result = _swallow_stdout(
        lambda: model.transcribe(audio, batch_size=4, language=args.language or None),
        args.progress_file, 5, 40)

    align_language = result.get("language") or args.language or "zh"
    # 公开模型（wav2vec2 对齐）走 hf 镜像更快更稳
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    _report_progress(args.progress_file, 47, "加载对齐模型")
    align_model, metadata = _swallow_stdout(
        lambda: whisperx.load_align_model(language_code=align_language, device=device),
        args.progress_file, 47, 3)
    _report_progress(args.progress_file, 50, "词级对齐")
    result = _swallow_stdout(
        lambda: whisperx.align(result["segments"], align_model, metadata, audio, device,
                               return_char_alignments=False),
        args.progress_file, 50, 20)

    # pyannote 是门禁模型，必须直连 huggingface.co 并携带 token
    os.environ.pop("HF_ENDPOINT", None)
    if args.token:
        os.environ["HF_TOKEN"] = args.token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = args.token
    _report_progress(args.progress_file, 72, "加载说话人分离模型")
    from whisperx.diarize import DiarizationPipeline
    diarize_model = _swallow_stdout(
        lambda: DiarizationPipeline(token=args.token, device=device),
        args.progress_file, 72, 3)
    _report_progress(args.progress_file, 75, "说话人分离")
    diarize_segments = _swallow_stdout(lambda: diarize_model(audio),
                                       args.progress_file, 75, 20)
    result = _swallow_stdout(lambda: whisperx.assign_word_speakers(diarize_segments, result),
                             args.progress_file, 95, 4)
    _report_progress(args.progress_file, 99, "整理输出")

    raw_segments = []
    for seg in result.get("segments", []):
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        raw_segments.append({"start": round(float(seg.get("start") or 0), 2),
                             "end": round(float(seg.get("end") or 0), 2),
                             "text": text,
                             "speaker": seg.get("speaker") or ""})

    speakers = [s["speaker"] for s in raw_segments if s["speaker"]]
    labels = speaker_label_map(speakers)
    for seg in raw_segments:
        seg["speaker_label"] = labels.get(seg["speaker"], "")

    paragraphs = group_paragraphs(raw_segments)
    lines = []
    for p in paragraphs:
        label = p.get("speaker_label")
        prefix = f"【{fmt_ts(p['start'])}】"
        if label:
            prefix += f"【{label}】"
        lines.append(prefix + p["text"])

    print(json.dumps({
        "text": "\n\n".join(lines),
        "paragraphs": paragraphs,
        "segments": raw_segments,
        "diarized": True,
        "language": align_language,
        "duration": round(float(audio.shape[0]) / 16000, 2),
        "model": args.model,
        "elapsed": round(time.time() - started, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
