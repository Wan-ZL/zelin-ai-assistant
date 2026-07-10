"""One-click voice-profile generation (docs/VOICE.md — "Generate your own profile").

``python -m act.voice_gen`` is what the 设置页 "生成语气档案" button runs (and it
works as a plain CLI too). One pass:

1. Headless ``claude -p`` with the USER-level Slack MCP restricted to the same
   READ-ONLY tool group as the radar fallback (radar_slack._MCP_ALLOWED_TOOLS —
   never send/draft/reaction/canvas/schedule): search 100–200 messages the
   owner SENT (from:me, across DMs / group DMs / channels), then induce the
   owner's own profile using config/voice-profile.default.md as the structural
   template, outputting only the markdown full text.
2. The module validates the skeleton (must contain 全局铁律 / 桶 / 反面清单)
   BEFORE touching anything on disk. An existing state/voice-profile.md is
   backed up to ``voice-profile.md.bak-<ts>`` first, then the new profile is
   written atomically. Analytics beacon: ``voice_gen{ok, chars}``.
3. Any failure (no Slack MCP / timeout / skeleton mismatch) exits non-zero
   with ONE plain-language line on stdout (the settings page shows it verbatim)
   and NEVER overwrites the old profile.

Call-pattern notes (same landmines as act/radar_slack._default_mcp_runner):
- prompt must come BEFORE --allowedTools (the claude CLI parses --allowedTools
  as variadic and would swallow a trailing positional prompt);
- binary via radar._claude_bin (launchd/cron PATH 兜底), env via
  executor._runner_env (Keychain-less API-key fallback).

The runner is injectable so tests never spawn a real claude.

Run standalone:  python -m act.voice_gen
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from act.lib import analytics, config, failures, sanitize

# Read/search-only Slack MCP tool group — single source of truth is the radar
# fallback's red-line list (never add write tools THERE either).
from act.radar_slack import _MCP_ALLOWED_TOOLS

TIMEOUT_S = 600

# skeleton markers the induced profile MUST contain (same section layer as
# config/voice-profile.default.md); anything missing one is rejected unwritten.
_SKELETON_MARKERS = ("全局铁律", "桶", "反面清单")
_MIN_CHARS = 400   # a marker-only stub is not a profile

_PROMPT = """你在为 {owner} 生成"语气档案"（voice profile）。分两步：

第一步：用可用的 Slack 只读工具（搜索/读频道/读 thread），搜集 {owner} **自己发出**的消息
100–200 条（用 from:me 一类的检索方式，时间范围约最近 6 个月），覆盖 DM、群 DM 和频道，
并尽量覆盖多种语境：请求/求助、对 manager 的 DM、频道公告/分享、技术升级/证据链、中文闲聊。

第二步：以下面【结构模板】为骨架，归纳出 **{owner} 本人**的语气档案：
- 保持同样的骨架分层：全局铁律（若干条）、若干个"桶 X：…"语境分节（每桶一行模式描述
  + 4–7 条例句）、以及"反面清单"。骨架标题必须保留这些字样：全局铁律 / 桶 / 反面清单。
- 铁律与桶的规则必须来自 {owner} 真实消息里观察到的写作习惯（不是模板作者的习惯）；
  模板里与 {owner} 实际写法不符的规则要改掉。
- 每个桶的例句必须**逐字**引用 {owner} 的真实消息（可截取，不改写）。
- 只保留 {owner} 真实存在的语境桶；模板里的桶只是示意，可增可删可改名。

【结构模板开始】
{template}
【结构模板结束】

只输出新档案的 markdown 全文。不要任何解释、开场白、结尾总结或代码围栏。
"""

# template missing (e.g. a stripped-down install): describe the skeleton inline
# so the induction can still run — validation below is on the OUTPUT anyway.
_TEMPLATE_FALLBACK = (
    "（默认模板文件缺失——按此骨架输出：`# Voice Profile` 标题；`## 全局铁律（所有语境）`"
    "带编号规则；每个语境一节 `## 桶 X：<语境名>`，一行模式描述 + 4–7 条逐字例句；"
    "最后 `## 反面清单（草稿出现以下任何一条 = 重写）` 列出会立刻暴露\"不像本人\"的写法。）"
)


def profile_path() -> Path:
    """The owner's PRIVATE profile (work data, gitignored) — docs/VOICE.md."""
    return config.STATE_DIR / "voice-profile.md"


def template_path() -> Path:
    """The sanitized author default that ships with the repo."""
    return config.HOME / "config" / "voice-profile.default.md"


def build_prompt(cfg: config.Config) -> str:
    owner = (getattr(cfg, "owner_name", "") or "").strip() or "用户"
    try:
        template = template_path().read_text(encoding="utf-8").strip()
    except OSError:
        template = _TEMPLATE_FALLBACK
    return _PROMPT.format(owner=owner, template=template)


def _default_runner(prompt: str) -> subprocess.CompletedProcess:
    from act.executor import _runner_env
    from act.radar import _claude_bin   # cron/launchd PATH 兜底（radar.py 事故注）
    prompt, _ = sanitize.scrub(prompt)
    return subprocess.run(
        # NOTE: prompt must come BEFORE --allowedTools — the claude CLI parses
        # --allowedTools as variadic and would swallow a trailing positional
        # prompt (same landmine as radar_slack._default_mcp_runner).
        [
            _claude_bin(), "-p", prompt,
            "--output-format", "text",
            "--allowedTools", _MCP_ALLOWED_TOOLS,
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        env=_runner_env(),
    )


def _clean_output(raw: str) -> str:
    """Strip an accidental ```/```markdown fence around the whole document."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def validate_profile(text: str) -> Optional[str]:
    """None when ``text`` looks like a real profile, else the missing-part note
    (used inside the human error line)."""
    if len(text) < _MIN_CHARS:
        return failures.pick("内容过短", "output too short")
    missing = [m for m in _SKELETON_MARKERS if m not in text]
    if missing:
        return failures.pick("缺少 ", "missing ") + "/".join(missing)
    return None


def _backup_existing(path: Path) -> Optional[Path]:
    """Copy an existing profile aside as ``<name>.bak-<ts>`` (never destructive)."""
    if not path.exists():
        return None
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(path.name + f".bak-{ts}")
    bak.write_bytes(path.read_bytes())
    return bak


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def generate(cfg: Optional[config.Config] = None,
             runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None,
             ) -> tuple[bool, str]:
    """One generation pass. Returns (ok, one-line human message).

    The old profile is NEVER overwritten on failure: validation happens before
    any disk write, and a success first copies the old file to ``.bak-<ts>``.
    """
    if cfg is None:
        cfg = config.load_config()
    if runner is None:
        runner = _default_runner
    prompt = build_prompt(cfg)

    def _fail(reason: str, msg: str) -> tuple[bool, str]:
        analytics.log_event("voice_gen", ok=False, chars=0,
                            reason=analytics.clip(reason, 120))
        return False, msg

    try:
        proc = runner(prompt)
    except (OSError, subprocess.SubprocessError) as e:   # incl. TimeoutExpired
        return _fail(
            f"{type(e).__name__}: {e}",
            failures.pick(
                "生成超时或无法启动 claude，请稍后重试。旧档案未改动。",
                "Generation timed out or claude could not start; please retry "
                "later. The old profile is untouched.",
            ),
        )
    if getattr(proc, "returncode", 1) != 0:
        err = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        return _fail(
            f"exit {getattr(proc, 'returncode', '?')}: {err}"[:200],
            failures.pick(
                "生成失败：claude 运行出错（常见原因：Slack MCP 未接入或未授权）。旧档案未改动。",
                "Generation failed: claude exited with an error (most common "
                "cause: the Slack MCP server is not connected). The old "
                "profile is untouched.",
            ),
        )

    text = _clean_output(getattr(proc, "stdout", "") or "")
    problem = validate_profile(text)
    if problem:
        return _fail(
            f"skeleton: {problem}",
            failures.pick(
                f"生成结果不符合档案骨架（{problem}），已拒绝写入。旧档案未改动。",
                f"The generated text does not match the profile skeleton "
                f"({problem}); refused to save it. The old profile is untouched.",
            ),
        )

    dest = profile_path()
    bak = _backup_existing(dest)
    _atomic_write(dest, text if text.endswith("\n") else text + "\n")
    analytics.log_event("voice_gen", ok=True, chars=len(text))
    msg = failures.pick(
        f"已生成你的语气档案：{dest}", f"Voice profile generated: {dest}")
    if bak is not None:
        msg += failures.pick(f"（旧档案已备份为 {bak.name}）",
                             f" (previous profile backed up as {bak.name})")
    return True, msg


def _main(argv: Optional[list[str]] = None,
          runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voice_gen",
        description="Induce state/voice-profile.md from your own Slack messages "
                    "(read-only Slack MCP; docs/VOICE.md).")
    parser.parse_args(argv)
    ok, msg = generate(runner=runner)
    print(msg)   # stdout either way — the settings page shows this line verbatim
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
