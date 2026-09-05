pr: `fix/parity-slack-directory-ui-lang`
phase: P4 余量——behaviour-parity 审计批 `slack-directory-ui-lang`（chain secrets 第 3 批，接 #249；D3 原生 = 终版规格）
law: §68.1 追记（`GET /api/slack/directory` add-only query `lang=zh|en` → 子进程 `AIASSISTANT_UI_LANG`）；§15 语言解析顺序不动；§68.4 追记的 `doctor_run.parse_lang` 复用

**审计确认的一条丢失行为**（gap id：`settings-python-copy-ui-language`）。原生 SettingsSlack.swift:330 起 `act.lib.slack_setup --directory` 时带 `env["AIASSISTANT_UI_LANG"] = LanguageMirror.current`，act 侧 `error_message` → `failures.pick` 挑的双语失败句才与 app 语言一致；server/slack_directory 经 subproc 起同一条命令时只给 `AIASSISTANT_HOME`，web 把 `message` 原文显示——看板语言与守护进程的持久化 `language` / locale 不一致时（`settings-language-two-switches` 让这成为可能）那一句会以另一种语言出现。审计逐个查过 server 起的三个子进程，只有这一个吐语言句（claude-sessions `--scan` / syncd `--pair` 没有），所以只补这一处。

**做法**：机械半边照 §68.4 追记 2)（doctor 的 `?lang=`）同款——route 用 `doctor_run.parse_lang` 校验（缺席 / 空不注入、其它值 400 不 spawn），`slack_directory.directory(lang=)` 经 `subproc.run_module(extra_env=)` 进 env；web `fetchSlackDirectory(refresh, lang?)` 带 `useI18n().language`，手点 / 刷新 / 验证成功后的自动加载都带；切语言不重拉（原生 `.onChange(of: i18n.lang)` 只 `refreshStatus`）。哪把语言旋钮是真源本批不裁。

**判例**：`tests/test_server_slack_directory_lang.py`（env 键 / 与 refresh 同行 / 缺席不注入 / 坏值 400 且不 spawn / ok:false 原样透传 / 直接调函数）、web `SlackDirectoryPicker.lang.test.tsx`（zh / en / 自动加载 / 失败句原文 / 切语言不重拉）、`api.slackDirectoryLang.test.ts`（query 形）。
