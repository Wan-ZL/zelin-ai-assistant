// MCP servers（只读，隐私掩码在 server；§68.9）。Skills 商店住在 SkillsSection.tsx（§67）。
// Skills：三作用域（用户 ~/.claude/skills、项目 <repo>/.claude/skills、仓库商店 <repo>/skills）；
// 仓库商店条目带 enabled（= 用户级同名 symlink 指向它）；启用 / 停用按钮留 P7（R2.7.2）。
import { useEffect } from "react";
import { useI18n } from "../../i18n";
import { refreshMcp, useAppState } from "../../store";

type Text = (zh: string, en: string) => string;

export function scopeLabel(scope: string, text: Text): string {
  switch (scope) {
    case "user": return text("用户级", "User scope");
    case "project": return text("项目级", "Project scope");
    case "repo": return text("仓库商店", "Repo store");
    default: return scope;
  }
}

export function McpSection() {
  const { text } = useI18n();
  const { mcp, pageErrors } = useAppState();
  useEffect(() => {
    if (!mcp) void refreshMcp();
  }, [mcp]);

  return (
    <section className="settings-section" id="settings-mcp" aria-labelledby="settings-mcp-title">
      <h3 id="settings-mcp-title" className="settings-section-title">{text("MCP servers（Claude Code 外接工具）", "MCP servers (Claude Code external tools)")}</h3>
      <p className="settings-helper">
        {text("只读展示 user 与 project 两个作用域；增删改在终端用 claude mcp add / remove。env 值绝不显示，URL 与参数里的密钥已打码。", "Read-only view of the user and project scopes; add or remove with claude mcp add / remove in a terminal. env values are never shown; secrets in URLs / args are masked.")}
      </p>
      {pageErrors.mcp && <p className="settings-error" role="alert">{pageErrors.mcp}</p>}
      <div className="settings-actions">
        <button type="button" className="btn" onClick={() => void refreshMcp()}>{text("刷新", "Refresh")}</button>
      </div>
      {mcp?.scopes.map((scope) => (
        <div key={scope.scope} className="settings-subblock">
          <div className="settings-subhead">
            <span>{scopeLabel(scope.scope, text)}</span>
            <span className="settings-list-dim"> · {scope.path}</span>
            <span className="chip chip-quiet">{text(`${scope.servers.length} 个 server`, `${scope.servers.length} servers`)}</span>
          </div>
          {!scope.exists && <p className="settings-helper">{text("文件不存在——这个作用域还没配置过 MCP server。", "File not found — no MCP servers configured in this scope yet.")}</p>}
          {scope.exists && !scope.parseable && <p className="settings-warning">{text("JSON 解析失败——用编辑器检查语法。", "Couldn't parse the JSON — check the syntax in an editor.")}</p>}
          {scope.parseable && scope.exists && scope.servers.length === 0 && <p className="settings-helper">{text("文件里还没有 mcpServers 条目。", "No mcpServers entry in the file yet.")}</p>}
          {scope.servers.length > 0 && (
            <ul className="settings-list">
              {scope.servers.map((server) => (
                <li key={server.name} className="settings-list-row">
                  <span className="settings-list-title"><code>{server.name}</code>{server.incomplete === true && <span className="settings-warning"> {text("（配置不完整）", "(incomplete config)")}</span>}</span>
                  <span className="settings-list-meta">
                    <span className="chip chip-quiet">{scope.scope === "project" ? text("项目", "project") : text("用户", "user")}</span>
                    <span className="chip">{server.transport}</span>
                    {server.env_count > 0 && <span className="chip chip-quiet">{text(`env ×${server.env_count}`, `env ×${server.env_count}`)}</span>}
                  </span>
                  <p className="settings-list-dim">{server.summary}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  );
}
