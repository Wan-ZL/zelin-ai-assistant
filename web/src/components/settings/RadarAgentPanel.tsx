// 后台雷达行 + 运行状态行（CONTRACT §48.7；原生 SettingsGmail / SettingsSlack 的 agentRow + healthRow，
// 标签逐字镜像）。新架构里 Slack / Gmail 雷达仍是各自的 launchd agent（install.sh 步 5 渲染 + 加载），所以：
//   · 状态 = GET /api/radars（server 问 launchd 本人；「已安装，每 N 分钟自动运行」的 N 读模板 StartInterval）；
//     检查中… = 还没回；状态未知 = 非 darwin / 问不到；
//   · 「重新安装」= POST /api/radars/reinstall {source}（server 跑 install.sh --reinstall-agent <label>，同一个渲染器）
//     → 「已重新安装 ✓」+ 回执里 server 装完再问 launchd 的 loaded / 错误原文；
//   · 「立即测试一轮」= inbox 特形 radar_test_round {source}（actd 分离起 act.radar_<src> --once），
//     结果回到看板：radar_sources.<src>.test_round（running → done / noop / lost）+ health 的 last_attempt；
//     按钮在回执落地或 90 s 兜底前显示「测试中…」；
//   · 运行状态行（sourceHealth.RunStatusLine）拿同一份 interval_s 说「还没有运行记录。等一轮（≤N 分钟）…」。
import { useEffect, useRef, useState } from "react";
import { fetchRadarAgents, postAction, postRadarReinstall } from "../../api";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import type { RadarAgentStatus, RadarTestRound } from "../../types";
import { RunStatusLine } from "./sourceHealth";
import { errorMessage } from "./useToast";

export type RadarSource = "gmail" | "slack";

const TEST_ROUND_TIMEOUT_MS = 90_000;

type Text = (zh: string, en: string) => string;

/** agent 行的状态词（原生 agentRow 三态 + 非 darwin 的「状态未知」） */
export function agentStatusLabel(agent: RadarAgentStatus | null | undefined, text: Text): string {
  if (agent === undefined) return text("检查中…", "checking…");
  if (!agent || agent.loaded === null) return text("状态未知", "unknown");
  if (!agent.loaded) return text("未安装", "not installed");
  const minutes = agent.interval_s ? Math.round(agent.interval_s / 60) : null;
  return minutes
    ? text(`已安装，每 ${minutes} 分钟自动运行`, `installed — runs every ${minutes} minutes`)
    : text("已安装", "installed");
}

/** test_round 回执里值得说一句的状态（done 由运行状态行本身体现，不重复） */
export function testRoundNote(round: RadarTestRound | null | undefined, text: Text): string | null {
  if (!round) return null;
  if (round.state === "noop") {
    return round.note === "disabled"
      ? text("这一轮没跑：源开关是关的——先打开上方开关", "This round did not run: the source is switched off — enable it above first")
      : text("这一轮没跑：雷达进程没能启动（看 state/radar_test_round.log）", "This round did not run: the radar process failed to start (see state/radar_test_round.log)");
  }
  if (round.state === "lost") {
    return text("这一轮没有回音：超过 10 分钟没写运行记录（看 state/radar_test_round.log）", "No word from this round: no run record for over 10 minutes (see state/radar_test_round.log)");
  }
  return null;
}

export function RadarAgentPanel({ source }: { source: RadarSource }) {
  const { text } = useI18n();
  const { board } = useAppState();
  const health = board?.radar_sources?.[source];
  const [agent, setAgent] = useState<RadarAgentStatus | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);
  // 点「立即测试一轮」时看到的上一份回执 requested_at；新回执落地（且不再 running）即结束忙态
  const [testing, setTesting] = useState<{ prev: string } | null>(null);
  const timer = useRef<number | null>(null);

  async function refresh() {
    setAgent(undefined);
    try {
      const snap = await fetchRadarAgents();
      setAgent(snap.radars[source] ?? null);
    } catch {
      setAgent(null);
    }
  }

  useEffect(() => {
    void refresh();
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  const round = health?.test_round ?? null;
  useEffect(() => {
    if (!testing) return;
    if (round && round.requested_at !== testing.prev && round.state !== "running") setTesting(null);
  }, [round, testing]);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  async function reinstall() {
    setBusy(true);
    setNote({ ok: true, message: text("正在重新安装后台雷达…", "Reinstalling the background radar…") });
    try {
      const receipt = await postRadarReinstall(source);
      setNote({ ok: true, message: text("已重新安装 ✓", "Reinstalled ✓") });
      // 回执里的 loaded 是 server 装完后再问 launchd 的答案——就是最新真相，不再多拉一次
      setAgent((prev) => ({ label: receipt.label, interval_s: prev?.interval_s ?? null, plist_installed: true, ...prev, loaded: receipt.loaded }));
    } catch (err) {
      setNote({ ok: false, message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function testRound() {
    setNote(null);
    setTesting({ prev: round?.requested_at ?? "" });
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setTesting(null), TEST_ROUND_TIMEOUT_MS);
    try {
      await postAction({ action: "radar_test_round", source });
    } catch (err) {
      setTesting(null);
      setNote({ ok: false, message: errorMessage(err) });
    }
  }

  const tone = agent === undefined || !agent || agent.loaded === null ? "" : agent.loaded ? " is-ok" : " is-warning";
  const roundNote = testRoundNote(round, text);

  return (
    <div className="settings-radar" data-source={source}>
      <div className="settings-radar-row">
        <span className={`settings-dot${tone}`} aria-hidden="true" />
        <strong className="settings-radar-title">{text("后台雷达", "Background radar")}</strong>
        <span className="settings-helper settings-radar-status">{agentStatusLabel(agent, text)}</span>
        <span className="settings-radar-spacer" />
        <button type="button" className="btn" disabled={busy} onClick={() => void reinstall()}>
          {text("重新安装", "Reinstall")}
        </button>
      </div>
      {note && (
        <p className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>{note.message}</p>
      )}
      <div className="settings-subhead">{text("运行状态（真实轮询结果）", "Run status (real poll results)")}</div>
      <div className="settings-radar-row">
        {/* 「还没有运行记录。等一轮（≤N 分钟）…」的 N 与上一行「每 N 分钟自动运行」同源（launchd 模板 StartInterval） */}
        <RunStatusLine health={health} intervalS={agent?.interval_s ?? null} />
        <span className="settings-radar-spacer" />
        <button type="button" className="btn" disabled={testing !== null || !health?.enabled} onClick={() => void testRound()}>
          {testing ? text("测试中…", "Testing…") : text("立即测试一轮", "Test one round now")}
        </button>
        <button type="button" className="btn" onClick={() => void refresh()}>{text("刷新", "Refresh")}</button>
      </div>
      {roundNote && <p className="settings-warning" role="status">{roundNote}</p>}
    </div>
  );
}
