// server-owned 双语文案的取键（防腐 #10）：settings 目录 / 凭证 label / FDA 备注都以
// {zh, en} 两键下发，web 只按当前 UI 语言取键、逐字镜像，绝不在 client 再写一份。
import type { Language } from "../../i18n";
import type { BilingualText } from "../../types";

export function pickText(value: BilingualText | undefined | null, language: Language): string {
  if (!value) return "";
  const chosen = value[language];
  if (typeof chosen === "string" && chosen) return chosen;
  return typeof value.en === "string" ? value.en : "";
}
