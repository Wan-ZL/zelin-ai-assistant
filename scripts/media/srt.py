"""字幕生成器：M/demo.zh.srt / M/demo.en.srt（CONTRACT §77 拟；QA 面 §58）。

时间轴 = 分镜的计划时长累加——剪辑（assemble.sh）把每段素材裁到同一个计划时长，所以字幕、
配音、画面三者按同一把尺子对齐，不做二次测量（测量会随机器漂移）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shots as shots_mod  # noqa: E402


def stamp(seconds: float) -> str:
    """SRT 时间戳 HH:MM:SS,mmm（毫秒截断，不四舍五入——末尾不许越过片长）。"""
    if seconds < 0:
        raise ValueError("negative timestamp")
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total = total_ms // 1000
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d},{ms:03d}"


def cues(shots: List[Dict[str, Any]], lang: str, tail_gap: float = 0.2) -> List[Dict[str, Any]]:
    """每个镜头一条字幕；结束时间比下一条的开始早 `tail_gap` 秒（播放器不把两条粘在一起）。"""
    out = []
    for index, row in enumerate(shots_mod.timeline(shots), start=1):
        end = max(float(row["start"]), row["end"] - tail_gap)
        out.append({"index": index, "start": float(row["start"]), "end": end, "text": row["shot"][lang]})
    return out


def render(shots: List[Dict[str, Any]], lang: str, tail_gap: float = 0.2) -> str:
    blocks = []
    for cue in cues(shots, lang, tail_gap):
        blocks.append(f"{cue['index']}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{cue['text']}\n")
    return "\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="write demo.zh.srt / demo.en.srt")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--shots", default=None)
    args = ap.parse_args(argv)
    data = shots_mod.load(args.shots)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for lang in ("zh", "en"):
        path = outdir / f"demo.{lang}.srt"
        path.write_text(render(data["shots"], lang), encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
