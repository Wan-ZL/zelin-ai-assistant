// MCP servers（只读，隐私掩码在 server；§68.9 + 追记）。Skills 商店住在 SkillsSection.tsx（§67）。
// 原生 SettingsMCP.swift 的读侧逐字：刷新 → 总计行「共 N 个 server（用户 X · 项目 Y）」；两个作用域都空 → 空态句 +
// 可选中的 `claude mcp add -s user <name> -- <command>`（-s user 是故意的：CLI 默认 local 作用域不在扫描面）；
// 每行 transport 章按 http 蓝 / sse 紫 / stdio 灰（chip-transport-<t>），env 只给个数、tooltip 说明绝不显示值；
// 每个作用域一颗「在 Finder 显示」= POST /api/reveal {target:"mcp_user"|"mcp_project"}（路径 server 推导，文件不在时禁用）；
// 路径展示读 server 的 add-only path_display（$HOME → ~；老 server 缺席退回 path）。
import { useEffect, useState } from "react";
import { postRevealTarget, type RevealTarget } from "../../api";
import { useI18n } from "../../i18n";
import { refreshMcp, useAppState } from "../../store";
import type { McpScope } from "../../types";
import { errorMessage } from "./useToast";

type Text = (zh: string, en: string) => string;

export function scopeLabel(scope: string, text: Text): string {
  switch (scope) {
    case "user": return text("用户级", "User scope");
    case "project": return text("项目级", "Project scope");
    case "repo": return text("仓库商店", "Repo store");
    default: return scope;
  }
}

/** 作用域 → reveal 词表项（wire add-only：未知作用域不装按钮） */
export function scopeRevealTarget(scope: string): RevealTarget | null {
  if (scope === "user") return "mcp_user";
  if (scope === "project") return "mcp_project";
  return null;
}

function countIn(scopes: McpScope[], scope: string): number {
  return scopes.filter((s) => s.scope === scope).reduce((n, s) => n + s.servers.length, 0);
}

export function McpSection() {
  const { text } = useI18n();
  const { mcp, pageErrors } = useAppState();
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (!mcp) void refreshMcp();
  }, [mcp]);

  const total = mcp ? mcp.scopes.reduce((n, s) => n + s.servers.length, 0) : 0;

  async function reveal(scope: McpScope) {
    const target = scopeRevealTarget(scope.scope);
    if (!target) return;
    setNote(null);
    try {
      await postRevealTarget(target);
    } catch (err) {
      setNote(errorMessage(err));
    }
  }

  return (
    <section className="settings-section" id="settings-mcp" aria-labelledby="settings-mcp-title">
      <h3 id="settings-mcp-title" className="settings-section-title">{text("MCP servers（Claude Code 外接工具）", "MCP servers (Claude Code external tools)")}</h3>
      <p className="settings-helper">
        {text("只读展示 user 与 project 两个作用域；增删改在终端用 claude mcp add / remove。env 值绝不显示，URL 与参数里的密钥已打码。", "Read-only view of the user and project scopes; add or remove with claude mcp add / remove in a terminal. env values are never shown; secrets in URLs / args are masked.")}
      </p>
      {pageErrors.mcp && <p className="settings-error" role="alert">{pageErrors.mcp}</p>}
      <div className="settings-actions">
        <button type="button" className="btn" onClick={() => void refreshMcp()}>{text("刷新", "Refresh")}</button>
        {mcp && total > 0 && (
          <span className="settings-helper">
            {text(
              `共 ${total} 个 server（用户 ${countIn(mcp.scopes, "user")} · 项目 ${countIn(mcp.scopes, "project")}）`,
              `${total} servers (user ${countIn(mcp.scopes, "user")} · project ${countIn(mcp.scopes, "project")})`,
            )}
          </span>
        )}
      </div>
      {mcp && total === 0 && (
        <div className="mcp-empty">
          <p className="settings-helper">{text("两个作用域都还没有 MCP server。到终端里加一个，回来点「刷新」就能看到：", "No MCP servers in either scope yet. Add one in a terminal, then come back and hit Refresh:")}</p>
          <code className="mcp-add-hint">claude mcp add -s user &lt;name&gt; -- &lt;command&gt;</code>
        </div>
      )}
      {mcp?.scopes.map((scope) => (
        <div key={scope.scope} className="settings-subblock">
          <div className="settings-subhead-row">
            <div className="settings-subhead">
              <span>{scopeLabel(scope.scope, text)}</span>
              <span className="settings-list-dim"> · <code className="mcp-scope-path">{scope.path_display ?? scope.path}</code></span>
              <span className="chip chip-quiet">{text(`${scope.servers.length} 个 server`, `${scope.servers.length} servers`)}</span>
            </div>
            {scopeRevealTarget(scope.scope) && (
              <button type="button" className="btn" disabled={!scope.exists} onClick={() => void reveal(scope)}>{text("在 Finder 显示", "Reveal in Finder")}</button>
            )}
          </div>
          {!scope.exists && <p className="settings-helper">{text("文件不存在——这个作用域还没配置过 MCP server。", "File not found — no MCP servers configured in this scope yet.")}</p>}
          {scope.exists && !scope.parseable && <p className="settings-warning">{text("JSON 解析失败——点「在 Finder 显示」用编辑器检查语法。", "Couldn't parse the JSON — click \"Reveal in Finder\" and check the syntax in an editor.")}</p>}
          {scope.parseable && scope.exists && scope.servers.length === 0 && <p className="settings-helper">{text("文件里还没有 mcpServers 条目。", "No mcpServers entry in the file yet.")}</p>}
          {scope.servers.length > 0 && (
            <ul className="settings-list">
              {scope.servers.map((server) => (
                <li key={server.name} className="settings-list-row">
                  <span className="settings-list-title"><code>{server.name}</code>{server.incomplete === true && <span className="settings-warning"> {text("（配置不完整）", "(incomplete config)")}</span>}</span>
                  <span className="settings-list-meta">
                    <span className="chip chip-quiet">{scope.scope === "project" ? text("项目", "project") : text("用户", "user")}</span>
                    <span className={`chip chip-transport-${server.transport}`}>{server.transport}</span>
                    {server.env_count > 0 && (
                      <span className="chip chip-quiet" title={text("环境变量只显示数量——值可能含密钥，绝不显示。", "Env vars show as a count only — values may hold secrets and are never displayed.")}>
                        {text(`env ×${server.env_count}`, `env ×${server.env_count}`)}
                      </span>
                    )}
                  </span>
                  <p className="settings-list-dim" title={server.summary}>{server.summary}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {note && <p className="settings-warning" role="alert">{note}</p>}
    </section>
  );
}
