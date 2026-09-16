// Skills 区（CONTRACT §67 / §67.5，owner 决策 D13 / R2.7.2–R2.7.3）。D78（2026-09-15）起它的落点是**技能页**
// （`?page=skills`，pages/SkillsPage.tsx——同一个组件，不复制），设置页原处只剩 SkillsPointerSection 那一行入口。
// 一行一个仓库 skill（skills/index.yaml）：名字 + 版本 + 描述 + 本机状态徽章 + 启用/停用开关。
// 状态是 server 判的（enabled / disabled / copy / custom / foreign），client 只镜像 wire 键：
//   enabled  = ~/.claude/skills/<name> 软链接指向仓库副本（开关：停用）
//   copy     = 商店拷贝（无软链接的文件系统）；落后时 sync 会刷新（开关：停用）
//   custom   = 本地改过的副本——商店永不覆盖/删除，开关锁定，显示「自定义 · 落后/领先 N 版」
//   foreign  = 不是商店放的东西（别处的软链接 / 普通文件），开关锁定
// 切换 = 一次 POST {name, action}；server 拒绝（409 CONFLICT 等）的整句原文以 toast 显示。
// 原生 SettingsSkills 的两颗按钮也在：「刷新」（忙态「扫描中…」= 重拉快照）与每行的「在 Finder 显示」
// （POST /api/reveal {target:"skill", name}——server 选中该 skill 的 SKILL.md：本机副本优先、否则商店原件）。
// 原生「新建 skill」表单不搬：仓库 = 商店、skills/ 只有 git 写（§67.1 / §67.5 不做编辑器）。
import { useEffect, useState } from "react";
import { ApiError, postRevealTarget } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { refreshSkills, toggleSkill, useAppState } from "../../store";
import type { SkillRow } from "../../types";
import { errorMessage } from "./useToast";

const TOAST_MS = 6000;

/** 区标题（zh, en）——设置页的入口行与技能页共用同一对字面量，没有第二份
 *  （§66.2 `screen:settings.skills` 探针读的就是它；原生 SettingsSectionDescriptor 逐字） */
export const SKILLS_TITLE = ["Skills（Claude Code 技能）", "Skills (Claude Code)"] as const;

interface Toast {
  kind: "ok" | "error";
  message: string;
}

export function SkillsSection() {
  const { text } = useI18n();
  const { skills, skillsError } = useAppState();
  const [busyName, setBusyName] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    void refreshSkills();
  }, []);

  async function scan() {
    setScanning(true);
    try {
      await refreshSkills();
    } finally {
      setScanning(false);
    }
  }

  async function reveal(row: SkillRow) {
    setToast(null);
    try {
      await postRevealTarget("skill", row.name);
    } catch (error) {
      setToast({ kind: "error", message: errorMessage(error) });
    }
  }

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
      <h3 id="settings-skills-title" className="settings-section-title">{text(...SKILLS_TITLE)}</h3>
      <p className="settings-helper">
        {text(
          "仓库自带的 skill 商店（skills/）。启用 = 在 ~/.claude/skills 放一个指向仓库副本的软链接——Claude Code 与派工 agent 真正读取的位置；另一台机器 git pull 后跑 scripts/skills_sync.sh 即同步。本地改过的副本标为「自定义」，商店永不覆盖。",
          "The repo's own skill store (skills/). Enable = a symlink in ~/.claude/skills pointing at the repo copy — the place Claude Code and dispatched agents actually read; on another machine, git pull then scripts/skills_sync.sh. A locally edited copy is marked \"custom\" and never overwritten.",
        )}
      </p>

      {skillsError && !skills && <p className="settings-error" role="alert">{skillsError}</p>}
      {!skills && !skillsError && <p className="settings-helper">{text("读取中…", "Loading…")}</p>}

      <div className="settings-actions">
        <button type="button" className="btn" disabled={scanning} onClick={() => void scan()}>
          {scanning ? text("扫描中…", "Scanning…") : text("刷新", "Refresh")}
        </button>
        {skills && (
          // 原生 SettingsSkills 的计数行：共 N 个（用户级 = 已链进 ~/.claude/skills · 项目级 = 仓库内可见）
          <span className="settings-helper">
            {text(
              `共 ${skills.skills.length} 个（用户级 ${skills.skills.filter((r) => r.toggle === "disable").length} · 项目级 ${skills.skills.filter((r) => r.project_visible).length}）`,
              `${skills.skills.length} total (user ${skills.skills.filter((r) => r.toggle === "disable").length} · project ${skills.skills.filter((r) => r.project_visible).length})`,
            )}
          </span>
        )}
      </div>
      {skills && (
        <ul className="skills-list">
          {skills.skills.map((row) => (
            <SkillRowView
              key={row.name}
              row={row}
              isBusy={busyName === row.name}
              onToggle={() => void flip(row)}
              onReveal={() => void reveal(row)}
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
  onReveal: () => void;
}

function SkillRowView({ row, isBusy, onToggle, onReveal }: SkillRowViewProps) {
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
          {/* 原生 scope chip：用户（~/.claude/skills）/ 项目（仓库内 .claude/skills 软链接） */}
          {isOn && <span className="skill-row-badge is-user">{text("用户", "user")}</span>}
          {row.project_visible && (
            <span
              className="skill-row-badge is-project"
              title={text("通过仓库里的 .claude/skills 软链接，任何在本仓库工作的会话与 agent 都能看到", "Visible to every session and agent working in this repo via the tracked .claude/skills symlink")}
            >
              {text("项目", "project")}
            </span>
          )}
          {row.default_enabled && (
            <span className="skill-row-badge is-default">{text("默认开", "default on")}</span>
          )}
        </div>
        <p className="skill-row-desc">{row.description || text("无描述", "No description")}</p>
        {isLocked && (
          <p className="settings-warning">{lockedHint(row, text)}</p>
        )}
      </div>
      <div className="skill-row-actions">
        <button type="button" className="btn btn-quiet" onClick={onReveal}>{text("在 Finder 显示", "Reveal in Finder")}</button>
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
      </div>
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

/**
 * 设置页原「Skills」区位置留下的一行入口（D78）：区还在目录与设置搜索里（同一个 id `skills`、同一对标题字面量），
 * 正文只剩一句指路 + 一条到技能页的链接（`<a href="?page=skills">`——左键由 route 的文档级委托拦成 pushState，
 * ⌘点 / 复制链接照旧是完整深链）。`?anchor=skills` / `#settings-skills` 旧深链不落在这一行上：SettingsPage 见到
 * 这个锚点直接改道到技能页。
 */
export function SkillsPointerSection() {
  const { text } = useI18n();
  return (
    <section className="settings-section" aria-labelledby="settings-skills-moved-title">
      <h3 id="settings-skills-moved-title" className="settings-section-title">{text(...SKILLS_TITLE)}</h3>
      <p className="settings-helper">
        {text(
          "Skills 已搬到左侧导航栏的「技能」页——启用 / 停用、在 Finder 显示、版本与作用域都在那里。",
          "Skills now lives in the sidebar under \"Skills\" — enable/disable, reveal in Finder, versions and scopes are all there.",
        )}
      </p>
      <div className="settings-actions">
        <a className="btn" href={buildAppUrl(window.location.href, "skills", null).toString()}>
          {text("Skills 已搬到左侧「技能」页 →", "Skills moved to \"Skills\" in the sidebar →")}
        </a>
      </div>
    </section>
  );
}
