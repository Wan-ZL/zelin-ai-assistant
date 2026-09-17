"""演示视频的评委席（CONTRACT §77 拟；QA 面 §58）。

三席独立打分：`claude-fable-5-1` / `claude-opus-5`（Anthropic Messages API，image block）、
`gpt-5.5`（OpenAI chat completions，image_url）。每席收到同一份材料：rubric、1 fps 抽帧 +
每镜头一张关键帧（下采样、上限 `FRAME_CAP` 张）、两条字幕、中文旁白的 gpt-4o-transcribe 转写。
及格 = 单席总分 ≥ 7；全片通过 = 3 席里至少 2 席及格。缺 `FIREWORKS_API_KEY` 时 kimi-k3 席记
BLOCKED（不顶替、不删席、不计入 seats）。纯 stdlib（urllib）；key 只从环境读，永不打印。
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shots as shots_mod  # noqa: E402

HERE = Path(__file__).resolve().parent
RUBRIC_PATH = HERE / "rubric.md"
DIMENSIONS = ["真实性", "清晰", "可读", "节奏", "无误导"]
PASS_TOTAL = 7
PASS_SEATS = 2
FRAME_CAP = 40
FRAME_WIDTH = 640
ANTHROPIC_SEATS = ("claude-fable-5-1", "claude-opus-5")
OPENAI_SEAT = "gpt-5.5"
BLOCKED_SEAT = {"seat": "kimi-k3", "status": "BLOCKED", "reason": "FIREWORKS_API_KEY absent"}
TRANSCRIBE_MODEL = "gpt-4o-transcribe"

FFMPEG = os.environ.get("FFMPEG", str(Path.home() / ".local" / "bin" / "ffmpeg"))


# ---------------------------------------------------------------- frames / audio

def key_frame_seconds(shots: List[Dict[str, Any]]) -> List[int]:
    """每个镜头正中间那一秒——抽帧稀疏化之后也保证每个镜头至少有一张。"""
    return [row["start"] + (row["end"] - row["start"]) // 2 for row in shots_mod.timeline(shots)]


def pick_frames(available: List[int], keys: List[int], cap: int = FRAME_CAP) -> List[int]:
    """1 fps 的秒号里挑 ≤cap 张：关键帧全留，其余等距补齐（省 token，见 goal 的预算上限）。"""
    chosen = sorted({s for s in keys if s in available})
    if len(chosen) >= cap:
        return chosen[:cap]
    rest = [s for s in available if s not in chosen]
    room = cap - len(chosen)
    if rest and room > 0:
        step = max(1, len(rest) / room)
        chosen += [rest[min(len(rest) - 1, int(i * step))] for i in range(room)]
    return sorted(set(chosen))[:cap]


def extract_frames(video: Path, workdir: Path, shots: List[Dict[str, Any]], cap: int = FRAME_CAP) -> List[Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(video),
         "-vf", f"fps=1,scale={FRAME_WIDTH}:-2", "-q:v", "6", str(workdir / "f-%04d.jpg")],
        check=True,
    )
    frames = sorted(workdir.glob("f-*.jpg"))
    seconds = list(range(len(frames)))
    wanted = pick_frames(seconds, key_frame_seconds(shots), cap)
    return [frames[s] for s in wanted]


def extract_audio(video: Path, out: Path, track: int = 0) -> Path:
    subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(video), "-map", f"0:a:{track}",
         "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
        check=True,
    )
    return out


# ---------------------------------------------------------------- HTTP helpers

def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 600) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read()[:400].decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {err.code} from {url.split('//')[-1].split('/')[0]}: {body}") from None


def transcribe(audio: Path, api_key: str, timeout: int = 600) -> str:
    """gpt-4o-transcribe 的 multipart 上传（stdlib 手搓 body，不引 requests）。"""
    boundary = f"----zaa{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
    parts: List[bytes] = []
    for field, value in (("model", TRANSCRIBE_MODEL), ("response_format", "text")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode() + audio.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions", data=b"".join(parts),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"transcribe HTTP {err.code}: {err.read()[:300].decode('utf-8', 'replace')}") from None


# ---------------------------------------------------------------- prompt + parsing

def build_prompt(rubric: str, srt_zh: str, srt_en: str, transcript: str, duration: float, frame_count: int) -> str:
    return (
        "你是一位独立评委，只根据给到的材料给这段产品演示视频打分。材料：一段视频的抽帧（1 fps 采样 + 每个镜头一张关键帧，"
        f"共 {frame_count} 张，按时间顺序）、两条字幕轨、中文旁白的机器转写。视频总时长 {duration:.1f} 秒。\n\n"
        "评分表（每维 0/1/2，总分 10）：\n" + rubric + "\n\n"
        "中文字幕（SRT）：\n" + srt_zh + "\n\n英文字幕（SRT）：\n" + srt_en + "\n\n"
        "中文旁白的机器转写：\n" + transcript + "\n\n"
        "只输出一个 JSON 对象，不要 markdown 围栏、不要任何解释文字，形状严格如下：\n"
        '{"seat": "<你的席位名，原样回填>", "scores": {"真实性": 0-2, "清晰": 0-2, "可读": 0-2, "节奏": 0-2, "无误导": 0-2}, '
        '"total": <五项之和>, "verdict": "pass"|"fail", "fixes": ["<可执行的修改建议，最多 5 条；pass 时可为空数组>"]}\n'
        "verdict 的规则：total ≥ 7 是 pass，否则 fail。fixes 要具体到镜头（例如「第 7 镜设置页滚动过快，放慢或加停顿」）。"
    )


def _json_blob(text: str) -> str:
    """从模型回复里抠出 JSON 对象：允许 ```json 围栏、允许前后废话（LLM 输出不可信，宪法第 11 条）。"""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply (first 200 chars: {text[:200]!r})")
    return text[start:end + 1]


def _sanitize_scores(raw: Any) -> Dict[str, int]:
    """五维分数逐字段消毒：非数字即拒（bool 也算非数字——True 当 1 分那次事故成法），越界钳到 0–2。"""
    if not isinstance(raw, dict):
        raise ValueError("scores missing")
    scores: Dict[str, int] = {}
    for dim in DIMENSIONS:
        value = raw.get(dim)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"score for {dim} is not a number: {value!r}")
        scores[dim] = max(0, min(2, int(round(float(value)))))
    return scores


def _sanitize_fixes(raw: Any) -> List[str]:
    """修改建议：非列表当空、空白条目丢掉、最多 5 条。"""
    if not isinstance(raw, list):
        return []
    return [str(f).strip() for f in raw if str(f).strip()][:5]


def parse_seat_payload(seat: str, text: str) -> Dict[str, Any]:
    """逐字段消毒成席位记录；分数钳到 0–2、total 按分项重算、verdict 由 total 判定。"""
    data = json.loads(_json_blob(text))
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    scores = _sanitize_scores(data.get("scores"))
    total = sum(scores.values())
    fixes = _sanitize_fixes(data.get("fixes"))
    return {
        "seat": seat,
        "scores": scores,
        "total": total,
        "verdict": "pass" if total >= PASS_TOTAL else "fail",
        "fixes": fixes,
    }


def round_verdict(seats: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [s for s in seats if s.get("status") != "BLOCKED" and "total" in s]
    passing = [s for s in active if s["total"] >= PASS_TOTAL]
    return {"seats": len(active), "pass": len(passing), "verdict": "pass" if len(passing) >= PASS_SEATS else "fail"}


# ---------------------------------------------------------------- seats

def _image_blocks_anthropic(frames: List[Path]) -> List[Dict[str, Any]]:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(p.read_bytes()).decode("ascii")}} for p in frames]


def ask_anthropic(seat: str, prompt: str, frames: List[Path], api_key: str) -> str:
    payload = {
        "model": seat,
        # 8000：thinking 型的席位（claude-fable-5-1）在 2000 上会把预算烧在思考上、正文一个字都没吐出来
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": _image_blocks_anthropic(frames) + [{"type": "text", "text": prompt}]}],
    }
    data = _post_json("https://api.anthropic.com/v1/messages", payload,
                      {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")


def ask_openai(seat: str, prompt: str, frames: List[Path], api_key: str) -> str:
    content: List[Dict[str, Any]] = [
        {"type": "image_url",
         "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode("ascii"), "detail": "low"}}
        for p in frames
    ]
    content.append({"type": "text", "text": prompt})
    payload = {"model": seat, "messages": [{"role": "user", "content": content}]}
    data = _post_json("https://api.openai.com/v1/chat/completions", payload, {"Authorization": f"Bearer {api_key}"})
    return data["choices"][0]["message"].get("content") or ""


def run_round(prompt: str, frames: List[Path], anthropic_key: str, openai_key: str) -> List[Dict[str, Any]]:
    seats: List[Dict[str, Any]] = []
    for seat in ANTHROPIC_SEATS:
        seats.append(_seat_record(seat, lambda s=seat: ask_anthropic(s, prompt, frames, anthropic_key)))
    seats.append(_seat_record(OPENAI_SEAT, lambda: ask_openai(OPENAI_SEAT, prompt, frames, openai_key)))
    return seats


def _seat_record(seat: str, call) -> Dict[str, Any]:
    try:
        return parse_seat_payload(seat, call())
    except Exception as err:  # 一席失败不拖垮全席：记 error，round_verdict 不把它算进 active
        return {"seat": seat, "status": "ERROR", "reason": str(err)[:300]}


# ---------------------------------------------------------------- entry point

def video_duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _keys() -> tuple:
    """两把 key 都得在——缺任何一把都是 BLOCKED，不许少一席凑合跑（评委席不得顶替或删席）。"""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not anthropic_key or not openai_key:
        raise SystemExit("ANTHROPIC_API_KEY / OPENAI_API_KEY absent → BLOCKED (no substitute seat)")
    return anthropic_key, openai_key


def prepare_materials(media: Path, video: Path, shots: List[Dict[str, Any]], work: Path,
                      cap: int, openai_key: str) -> tuple:
    """抽帧 + 转写 + 拼 prompt：每席收到的材料一模一样（独立打分的前提是同一份卷子）。"""
    frames = extract_frames(video, work / "frames", shots, cap)
    audio = extract_audio(video, work / "narration-zh.mp3")
    transcript = transcribe(audio, openai_key)
    (work / "transcript.zh.txt").write_text(transcript, encoding="utf-8")
    prompt = build_prompt(
        RUBRIC_PATH.read_text(encoding="utf-8"),
        (media / "demo.zh.srt").read_text(encoding="utf-8"),
        (media / "demo.en.srt").read_text(encoding="utf-8"),
        transcript, video_duration(video), len(frames),
    )
    return frames, prompt


def record_round(out: Path, rnd: int, frames: int, seats: List[Dict[str, Any]], verdict: Dict[str, Any]) -> None:
    """把这一轮写进 judges.json（同号轮重跑 = 覆盖那一轮，别的轮不动）。"""
    doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"rounds": [], "blocked_seats": [BLOCKED_SEAT]}
    doc["blocked_seats"] = [BLOCKED_SEAT]
    doc["rounds"] = [r for r in doc.get("rounds", []) if r.get("round") != rnd]
    # 注意键序：`seats` 是席位记录的**列表**（judges.json 的形状），计数走 active_seats / passing_seats——
    # 别再用 **verdict 展开覆盖它（2026-09-15 写坏过一次：seats 变成了一个整数）
    doc["rounds"].append({"round": rnd, "frames": frames, "active_seats": verdict["seats"],
                          "passing_seats": verdict["pass"], "verdict": verdict["verdict"], "seats": seats})
    doc["rounds"].sort(key=lambda r: r["round"])
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="judge the demo video with three independent seats")
    ap.add_argument("--media", required=True, help="M (artefact dir)")
    ap.add_argument("--round", type=int, default=1, help="1 or 2 (round 2 = after applying round 1's fixes)")
    ap.add_argument("--shots", default=None)
    ap.add_argument("--cap", type=int, default=FRAME_CAP)
    args = ap.parse_args(argv)
    media = Path(args.media)
    video = media / "demo.mp4"
    if not video.exists():
        raise SystemExit(f"{video} missing — run assemble.sh first")
    anthropic_key, openai_key = _keys()

    data = shots_mod.load(args.shots)
    (media / "rubric.md").write_text(RUBRIC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    work = media / "judge-work" / f"round{args.round}"
    frames, prompt = prepare_materials(media, video, data["shots"], work, args.cap, openai_key)
    seats = run_round(prompt, frames, anthropic_key, openai_key)
    verdict = round_verdict(seats)

    out = media / "judges.json"
    record_round(out, args.round, len(frames), seats, verdict)
    print(f"round={args.round} seats={verdict['seats']} pass={verdict['pass']} verdict={verdict['verdict']} → {out}")
    return 0 if verdict["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
