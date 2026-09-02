// 设置页 section「Skills」（CONTRACT §65，owner 决策 D13 / R2.7.2–R2.7.3）。
// 一行一个仓库 skill（skills/index.yaml）：名字 + 版本 + 描述 + 本机状态徽章 + 启用/停用开关。
// 状态是 server 判的（enabled / disabled / copy / custom / foreign），client 只镜像 wire 键：
//   enabled  = ~/.claude/skills/<name> 软链接指向仓库副本（开关：停用）
//   copy     = 商店拷贝（无软链接的文件系统）；落后时 sync 会刷新（开关：停用）
//   custom   = 本地改过的副本——商店永不覆盖/删除，开关锁定，显示「自定义 · 落后/领先 N 版」
//   foreign  = 不是商店放的东西（别处的软链接 / 普通文件），开关锁定
// 切换 = 一次 POST {name, action}；server 拒绝（409 CONFLICT 等）的整句原文以 toast 显示。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSkills, toggleSkill, useAppState } from "../../store";
import type { SkillRow } from "../../types";

const TOAST_MS = 6000;

interface Toast {
  kind: "ok" | "error";
  message: string;
}

export function SkillsSection() {
  const { text } = useI18n();
  const { skills, skillsError } = useAppState();
  const [busyName, setBusyName] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    void refreshSkills();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  async function flip(row: SkillRow) {
    const action = row.toggle === "disable" ? "disable" : "enable";
    setBusyName(row.name);
    setToast(null);
    try {
      await toggleSkill(row.name, action);
      setToast({
        kind: "ok",
        message: action === "enable"
          ? text(`已启用 ${row.name}：Claude Code 与派工 agent 的下一个会话即可用。`, `${row.name} enabled — available to Claude Code and dispatched agents from their next session.`)
          : text(`已停用 ${row.name}。`, `${row.name} disabled.`),
      });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setBusyName(null);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="settings-skills-title">
      <h3 id="settings-skills-title" className="settings-section-title">{text("Skills", "Skills")}</h3>
      <p className="settings-helper">
        {text(
          "仓库自带的 skill 商店（skills/）。启用 = 在 ~/.claude/skills 放一个指向仓库副本的软链接——Claude Code 与派工 agent 真正读取的位置；另一台机器 git pull 后跑 scripts/skills_sync.sh 即同步。本地改过的副本标为「自定义」，商店永不覆盖。",
          "The repo's own skill store (skills/). Enable = a symlink in ~/.claude/skills pointing at the repo copy — the place Claude Code and dispatched agents actually read; on another machine, git pull then scripts/skills_sync.sh. A locally edited copy is marked \"custom\" and never overwritten.",
        )}
      </p>

      {skillsError && !skills && <p className="settings-error" role="alert">{skillsError}</p>}
      {!skills && !skillsError && <p className="settings-helper">{text("读取中…", "Loading…")}</p>}

      {skills && (
        <ul className="skills-list">
          {skills.skills.map((row) => (
            <SkillRowView
              key={row.name}
              row={row}
              isBusy={busyName === row.name}
              onToggle={() => void flip(row)}
            />
          ))}
        </ul>
      )}

      {skills && (
        <p className="settings-global-path">
          {text("链接位置", "Link location")}: {skills.skills_dir} · {text("仓库副本", "Repo copies")}: {skills.repo_skills_dir}
        </p>
      )}

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}

interface SkillRowViewProps {
  row: SkillRow;
  isBusy: boolean;
  onToggle: () => void;
}

function SkillRowView({ row, isBusy, onToggle }: SkillRowViewProps) {
  const { text } = useI18n();
  const isLocked = row.toggle === "locked";
  const isOn = row.toggle === "disable";
  return (
    <li className={`skill-row is-${row.state}`} data-skill={row.name}>
      <div className="skill-row-main">
        <div className="skill-row-head">
          <span className="skill-row-name">{row.name}</span>
          <span className="skill-row-version" title={row.upstream_version ?? undefined}>v{row.version}</span>
          <span className={`skill-row-badge is-${row.state}`}>{stateLabel(row, text)}</span>
          {row.project_visible && (
            <span
              className="skill-row-badge is-project"
              title={text("通过仓库里的 .claude/skills 软链接，任何在本仓库工作的会话与 agent 都能看到", "Visible to every session and agent working in this repo via the tracked .claude/skills symlink")}
            >
              {text("仓库内可见", "project-visible")}
            </span>
          )}
          {row.default_enabled && (
            <span className="skill-row-badge is-default">{text("默认开", "default on")}</span>
          )}
        </div>
        <p className="skill-row-desc">{row.description}</p>
        {isLocked && (
          <p className="settings-warning">{lockedHint(row, text)}</p>
        )}
      </div>
      <button
        type="button"
        className={`btn ${isOn ? "" : "btn-primary"}`.trim()}
        disabled={isLocked || isBusy}
        aria-label={text(`${isOn ? "停用" : "启用"} ${row.name}`, `${isOn ? "Disable" : "Enable"} ${row.name}`)}
        title={isLocked ? lockedHint(row, text) : undefined}
        onClick={onToggle}
      >
        {isBusy ? "…" : isOn ? text("停用", "Disable") : text("启用", "Enable")}
      </button>
    </li>
  );
}

type Text = (chinese: string, english: string) => string;

function distanceLabel(row: SkillRow, text: Text): string {
  if (row.relation === "behind") return text(`落后 ${row.distance} 版`, `${row.distance} behind`);
  if (row.relation === "ahead") return text(`领先 ${row.distance} 版`, `${row.distance} ahead`);
  if (row.relation === "unknown") return text("版本未知", "version unknown");
  return "";
}

/** 状态徽章文案：wire 枚举 → 双语；未知值原样展示（wire add-only） */
function stateLabel(row: SkillRow, text: Text): string {
  const distance = distanceLabel(row, text);
  switch (row.state) {
    case "enabled":
      return row.stale_target ? text("已启用 · 链接待刷新", "enabled · link stale") : text("已启用", "enabled");
    case "disabled":
      return text("已停用", "disabled");
    case "copy":
      return distance ? text(`副本 · ${distance}`, `copy · ${distance}`) : text("副本", "copy");
    case "custom":
      return distance ? text(`自定义 · ${distance}`, `custom · ${distance}`) : text("自定义", "custom");
    case "foreign":
      return text("非商店管理", "not managed");
    default:
      return row.state;
  }
}

function lockedHint(row: SkillRow, text: Text): string {
  if (row.state === "custom") {
    const installed = row.installed_version ? ` (v${row.installed_version})` : "";
    return text(
      `${row.path} 是本地改过的副本${installed}——商店不覆盖、不删除。要换回仓库版，先把它移走再启用。`,
      `${row.path} is a locally edited copy${installed} — the store never overwrites or deletes it. To use the repo version, move it away first, then enable.`,
    );
  }
  return text(
    `${row.path} 不是商店放的（指向别处的软链接或普通文件），请手动处理。`,
    `${row.path} was not placed by the store (a symlink elsewhere or a plain file); handle it by hand.`,
  );
}
