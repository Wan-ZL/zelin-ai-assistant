// 凭证区（§19 / §68.3；原生 Settings.swift credentialsGroup：标题与说明逐字镜像）：AI 引擎的 Anthropic API key
// 一行（SecretRow，保存即验证 = GET /v1/models）+ 一句「Slack token 与 Gmail 密码在各自接入区」。
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
    <section className="settings-section" id="settings-credentials" aria-labelledby="settings-credentials-title">
      <h3 id="settings-credentials-title" className="settings-section-title">
        {text("凭证（存本机 config/secrets/，保存后自动验证）", "Credentials (stored locally in config/secrets/; verified automatically on save)")}
      </h3>
      <SecretRow
        name="anthropic-api-key.txt"
        labelOverride="Anthropic API key"
        links={[{ label: text("控制台", "Console"), href: "https://console.anthropic.com/settings/keys" }]}
        helper={text("雷达提取的兜底 key（管线主路径走 Claude Code 登录，不需要它）。", "Fallback key for radar extraction (the main pipeline uses the Claude Code login and does not need it).")}
      />
      <p className="settings-helper">
        {text("Slack token 与 Gmail 密码在下面各自的接入区里粘贴（同样存本机、保存即验证）。", "The Slack token and Gmail password live in their own sections below (same local storage, same verify-on-save).")}
      </p>
    </section>
  );
}
