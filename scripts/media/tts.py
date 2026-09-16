"""旁白配音：OpenAI gpt-4o-mini-tts → M/audio/<lang>-<shot>.mp3（CONTRACT §77 拟；QA 面 §58）。

纯 stdlib（urllib）——运行时依赖白名单不变。key 只从环境读（`OPENAI_API_KEY`），
绝不打印、绝不落盘：出错时只回 HTTP 状态与 body 的前 300 字（body 里不含 key）。
一个镜头一段音频：与 shots.json 的镜头一一对应，剪辑（assemble.sh）按镜头时长伸缩对齐。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shots as shots_mod  # noqa: E402

ENDPOINT = "https://api.openai.com/v1/audio/speech"
MODEL = "gpt-4o-mini-tts"
VOICE = {"zh": "alloy", "en": "alloy"}
INSTRUCTIONS = {
    "zh": "语速稍快、口吻平实，像工程师给同事演示自己的工具；不夸张、不推销。",
    "en": "Brisk, plain-spoken, like an engineer demoing their own tool to a colleague. No hype, no sales voice.",
}


def speak(text: str, lang: str, out: Path, api_key: str, timeout: int = 180) -> int:
    """合成一段旁白，返回写下的字节数。"""
    payload = json.dumps({
        "model": MODEL,
        "voice": VOICE[lang],
        "input": text,
        "instructions": INSTRUCTIONS[lang],
        "response_format": "mp3",
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as err:  # 只回状态 + body 片段，永不回显请求头
        raise SystemExit(f"tts HTTP {err.code}: {err.read()[:300].decode('utf-8', 'replace')}") from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    return len(audio)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="synthesize the narration with gpt-4o-mini-tts")
    ap.add_argument("--outdir", required=True, help="M (artefact dir); audio lands in <outdir>/audio")
    ap.add_argument("--shots", default=None)
    ap.add_argument("--lang", default="zh,en")
    ap.add_argument("--force", action="store_true", help="re-synthesize even when the mp3 exists")
    args = ap.parse_args(argv)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY absent → BLOCKED (no substitute provider)")
    data = shots_mod.load(args.shots)
    audio_dir = Path(args.outdir) / "audio"
    made = 0
    for lang in args.lang.split(","):
        for shot in data["shots"]:
            out = audio_dir / f"{lang}-{shot['id']}.mp3"
            if out.exists() and not args.force:
                print(f"keep {out.name} ({out.stat().st_size} B)")
                continue
            size = speak(shot[lang], lang, out, api_key)
            made += 1
            print(f"wrote {out.name} ({size} B)")
    print(f"TTS shots={len(data['shots'])} langs={args.lang} new={made} dir={audio_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
