"""本地语音转写 CLI：由工作台服务端以子进程方式调用，向 stdout 输出 JSON。

依赖 faster-whisper（安装在项目 asr_venv 中）。模型文件首次使用时自动下载，
服务端已设置 HF_ENDPOINT=https://hf-mirror.com。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def fmt_ts(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def group_paragraphs(segments: list[dict], gap: float = 1.5) -> list[dict]:
    """把短句片段按停顿间隔合并为段落，避免逐行显示的臃肿输出。

    相邻片段间隔 <= gap 秒时并入同一段；每段保留起始时间戳便于回查录音。
    若片段带 speaker_label（说话人分离结果），说话人变化时强制另起一段，
    并把标签透传到段落上。
    """
    paragraphs: list[dict] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        speaker = str(segment.get("speaker_label") or "")
        if (paragraphs and start - paragraphs[-1]["end"] <= gap
                and (not speaker or speaker == paragraphs[-1].get("speaker_label", ""))):
            paragraphs[-1]["text"] += text
            paragraphs[-1]["end"] = float(segment.get("end") or start)
        else:
            paragraph = {"start": start, "end": float(segment.get("end") or start),
                         "text": text}
            if speaker:
                paragraph["speaker_label"] = speaker
            paragraphs.append(paragraph)
    return paragraphs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--glossary-file")
    args = parser.parse_args()

    started = time.time()
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    options: dict = {"vad_filter": True, "vad_parameters": {"min_silence_duration_ms": 500}}
    if args.language:
        options["language"] = args.language
    # 强制简体中文输出：whisper 对中文常默认繁体，用 initial_prompt 纠偏；
    # 同时抑制 condition_on_previous_text 的逐段漂移（防止幻觉累积、繁简摇摆）
    options["condition_on_previous_text"] = False
    prompt_parts = ["以下是中文会议录音的转写，请一律使用简体中文（白话文）输出，不要使用繁体字。"]
    if args.glossary_file and Path(args.glossary_file).exists():
        glossary = Path(args.glossary_file).read_text(encoding="utf-8").strip()
        if glossary:
            prompt_parts.append(
                "以下是本项目常用专有名词、缩写与人名，转写时遇到相近发音优先采用这些写法：\n"
                + glossary)
    options["initial_prompt"] = "\n".join(prompt_parts)

    segments, info = model.transcribe(args.audio, **options)
    output_segments = []
    parts = []
    for segment in segments:
        output_segments.append({"start": round(segment.start, 2),
                                "end": round(segment.end, 2),
                                "text": segment.text.strip()})
        parts.append(segment.text.strip())
    paragraphs = group_paragraphs(output_segments)
    print(json.dumps({
        "text": "\n\n".join(f"【{fmt_ts(p['start'])}】{p['text']}" for p in paragraphs),
        "paragraphs": paragraphs,
        "segments": output_segments,
        "language": info.language,
        "duration": round(info.duration, 2),
        "model": args.model,
        "elapsed": round(time.time() - started, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
