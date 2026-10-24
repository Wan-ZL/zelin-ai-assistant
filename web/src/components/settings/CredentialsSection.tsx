// 凭证区（§19 / §68.3；原生 Settings.swift credentialsGroup：标题逐字镜像）：Anthropic API key 一行（SecretRow，保存即验证 =
// GET /v1/models）。v2.0.0：这是唯一的凭证——§78 的 headless claude 只认 API key（`--bare` 永不读 Claude Code 登录 / 钥匙串），
// Slack token 与 Gmail 密码随各自的接入区退役。
// 锚 `credentials` 与原生冻结锚同名（依赖检查 / doctor 深链）。
import { useEffect } from "react";
import { useI18n } from "../../i18n";
import { refreshSecrets, useAppState } from "../../store";
import { SecretRow } from "./SecretRow";

export function CredentialsSection() {
  const { text } = useI18n();
  const { secrets } = useAppState();
  useEffect(() => {
    if (!secrets) void refreshSecrets();
  }, [secrets]);
  return (
    <section className="settings-section" aria-labelledby="settings-credentials-title">
      <h3 id="settings-credentials-title" className="settings-section-title">
        {text("凭证（存本机 config/secrets/，保存后自动验证）", "Credentials (stored locally in config/secrets/; verified automatically on save)")}
      </h3>
      <SecretRow
        name="anthropic-api-key.txt"
        labelOverride="Anthropic API key"
        links={[{ label: text("控制台", "Console"), href: "https://console.anthropic.com/settings/keys" }]}
        helper={text("会议纪要与笔记整理都用这把 key：app 的 claude 跑在自己的隔离目录里，不读你的 Claude Code 登录。", "Meeting recaps and note processing both use this key: the app runs claude in its own isolated directory and never reads your Claude Code login.")}
      />
    </section>
  );
}
