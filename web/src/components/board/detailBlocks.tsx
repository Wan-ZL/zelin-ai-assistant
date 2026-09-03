// 「展开详情」里的积木——镜像原生 Cards.swift 的 detail-slot building blocks：
//   PlanList  = PlanListView（📋 要做什么，编号；"[修改方向]" 行橙色加粗）
//   DodList   = DodListView（怎样算办完：编号）/ 待验收卡的 ☐ 验收清单
//   SourceList= SourceListView（💬 需求来自：who · channel · date + 引文斜体）
//   CopyPathLine（日志：/ 指令：一行等宽，点击复制 → ✓ 1.5s）
//   MetaLine（会话 ID：/ claude agents 列表名：等宽小字）
// 全部只读；不 import store。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import type { CardSource } from "../../types";
import { copyText } from "../detail/copyText";
import { CopiedAnnouncer } from "./cardChrome";

export function PlanList({ plan }: { plan: unknown }) {
  const { text } = useI18n();
  if (!Array.isArray(plan) || plan.length === 0) return null;
  return (
    <div className="card-detail-block">
      <div className="card-detail-heading">{text("📋 要做什么", "📋 Plan")}</div>
      <ol className="card-detail-list">
        {plan.map((step, i) => {
          const s = typeof step === "string" ? step : JSON.stringify(step);
          const rework = s.startsWith("[修改方向]");
          return <li key={i} className={rework ? "is-rework" : undefined}>{s}</li>;
        })}
      </ol>
    </div>
  );
}

export function DodList({ dod, heading, checklist = false }: { dod: unknown; heading?: string; checklist?: boolean }) {
  const { text } = useI18n();
  const items = Array.isArray(dod) ? dod.map((d) => (typeof d === "string" ? d : JSON.stringify(d))) : [];
  if (items.length === 0 && !checklist) return null;
  return (
    <div className="card-detail-block">
      {/* 原生 DodListView / 验收清单 头是 10 semibold（比 需求来自/要做什么 的 11 小一级） */}
      <div className="card-detail-subheading">{heading ?? text("怎样算办完：", "Definition of done:")}</div>
      {items.length === 0 ? (
        // §11 待验收：清单永远渲染，空时给兜底句（原生 ReviewRow）
        <p className="card-detail-muted">{text("该任务未定义验收标准，请自行判断", "No acceptance criteria defined — judge manually")}</p>
      ) : checklist ? (
        <ul className="card-detail-list is-checklist">{items.map((d, i) => <li key={i}>☐ {d}</li>)}</ul>
      ) : (
        <ol className="card-detail-list is-dod">{items.map((d, i) => <li key={i}>{d}</li>)}</ol>
      )}
    </div>
  );
}

export function SourceList({ sources }: { sources: unknown }) {
  const { text } = useI18n();
  if (!Array.isArray(sources) || sources.length === 0) return null;
  return (
    <div className="card-detail-block">
      <div className="card-detail-heading">{text("💬 需求来自", "💬 Requested by")}</div>
      {(sources as CardSource[]).map((s, i) => (
        <div key={i} className="card-source">
          <div className="card-source-who">{[s.who, s.channel, s.date].filter(Boolean).join(" · ")}</div>
          {s.quote && <div className="card-source-quote">{s.quote}</div>}
        </div>
      ))}
    </div>
  );
}

/** 一行等宽路径/命令，点击复制（原生 CopyPathLine：clipboard→✓ 1.5s） */
export function CopyPathLine({ label, path }: { label: string; path: unknown }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  if (typeof path !== "string" || !path) return null;
  return (
    <>
      <button
        type="button"
        className={`card-copy-path${copied ? " is-copied" : ""}`}
        title={path}
        onClick={() => {
          void copyText(path).then((ok) => {
            if (!ok) return;
            setCopied(true);
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        <span aria-hidden="true">{copied ? "✓ " : "⧉ "}</span>
        <span className="card-detail-label">{label}</span><span>{path}</span>
      </button>
      <CopiedAnnouncer copied={copied} />
    </>
  );
}

/** 等宽小字元信息行（会话 ID / claude agents 列表名） */
export function MetaLine({ label, value }: { label: string; value: unknown }) {
  if (typeof value !== "string" || !value) return null;
  return <p className="card-detail-mono"><span className="card-detail-label">{label}</span><span>{value}</span></p>;
}

/** 正文段（summary / delivered_summary）——空不渲染 */
export function BodyText({ value, className = "card-summary" }: { value: unknown; className?: string }) {
  if (typeof value !== "string" || !value) return null;
  return <p className={className}>{value}</p>;
}
