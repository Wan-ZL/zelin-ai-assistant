pr: `feat/screenpipe-prune-receipt`（issue #28 第二程，接在 #291 之后；#324 已关，本轮从它那里打捞媒体那一半）
phase: P4（mac-retire：retention UI re-home 到 web 设置页，D3「原生不加新功能」）
law: §72.4 / §72.5（新增小节，无新顶层 §）+ §18 / §15.3 / §68.1 三处 add-only 追记

**为什么还有第二程** — #291（已合进 dev）立了 §72.1–72.3：`GET /api/screenpipe/disk` 的占用快照与 `recording.retention_days`（db.sqlite 里的文本行）。媒体那一半仍是 `ingest/screenpipe-cleanup.sh` 里写死的两条 `find -mmin +60`：既改不动，也**看不见**——issue #28 评论里那条验收标准（「prune job 要可观测，不只是可配：停掉的 prune 与跑完没东西可删的 prune 从外面看一模一样，而前一种会永久丢数据/涨盘」）没有兑现。孪生 PR #324 做过这一半，但它的法条把前提写反了（「几个 GB 全在媒体上」「本键永不碰 db.sqlite」），#324 已关；本轮只打捞它正确的那部分：回执 + 保留期旋钮 + 真 bash 判例。

**实测背景（写进 §72.4，防止再被写反）** — 2026-09-14 owner 机器：`~/.screenpipe` 8.7 GB 里 `db.sqlite` 9,097,404,416 B、`data/` **0 B**。媒体那一半平时接近零，**正因为这个 prune 一直在跑**。所以这一程的价值不是「省下几个 GB」，而是窗口可调 + 停掉时看得见。

**做了什么** — ① 旋钮 `recording.media_retention_minutes`（overrides 扁平键 `screenpipe_media_retention_minutes`，出厂 60 = 现状不变，闭区间 [5, 525600]）**不另开 GET/PUT**：它是 storage 区的第二个目录字段（§68.1），文案 server-owned；目录因此长出一个通用的 `bounds` 机制——越界 400 并说清区间，**不夹取**（夹取会让设置页显示的数与 cron 真用的数不是一个），读到的越界值按缺席落到下一层，与 act 侧 coercer 同一条规则。② 脚本经 `python3 -m act.lib.config --print-value`（`--print-path` 的同族，silent-on-error 打出厂值）读同一层，设置页改完下一轮生效、无需重启。③ 每轮写回执 `state/screenpipe_prune.json`（`ok | no_data_dir | unreadable` + 删了几个文件几字节），**`unreadable` 单列**，脚本永远 exit 0（链是 `&&` 串的）。④ 快照 add-only 两键 `media_retention_minutes` / `media_prune`（回执原样 + server 算的 `age_seconds` / `stale`；缺席 / 坏形 = `never` + `stale`），设置页多一行「上次媒体清理」，停了 / 读不到进 warning 档并说出已经多久没跑（阈值 truth 在 server，web 不复刻）。⑤ §72.2 的 db 步现在跟着同一个数据目录走（`--db <数据目录的父>/db.sqlite`）——判例因此永不打开这台机器那份 8.5 GiB 的真库。

**判例** — 新 `tests/integration/test_screenpipe_cleanup.py`（真 bash、真文件、90 s 预算、两个 env 缝；删旧留新 / 三态回执 / override 压 yaml / 永远 exit 0）、`tests/test_screenpipe_media_retention_knob.py`（三层 + 区间 + 目录 bounds 与 act 逐字同一对数）、`tests/test_screenpipe_media_prune_projection.py`（staleness 与「跑了但没东西可删 ≠ 停了」；GET 路径把 `scan` / `db_stats` 桩成会抛，证明零阻塞 IO）、`tests/test_config_cli.py` 加 `--print-value` 五条、vitest 两处（`mediaPruneText` 各状态 + 停跑那一行的 alert；`bounds` 的草稿闸）。

**review 之后补的三处（同一个 PR）** — ① `find` 的退出码**不再丢掉**：清单落临时文件再遍历（`< <(find …)` 会把退出码吞掉），顶层读得到而某个子目录读不到时（真实布局 `~/.screenpipe/data/data/<日期>/`）那个子树一个文件都没被枚举到——以前报 `ok, deleted 0`，现在报新 state `partial`，判例里那个埋在 `chmod 000` 子目录下的旧 jpg 还在，回执没撒谎。② 回执多一键 `last_ok_ts` = **上次干净跑完**那一刻，失败的轮次原样带下去；`stale` 改按它算（不是按上一次尝试）——不然每 30 分钟失败一次的清理会把 `ts` 一直刷新、界面永远显示「刚跑过」而盘一直涨，issue 评论那条验收标准（「记下上次成功那一次，说出缺口有多大」）就只兑现了一半。③ `act` 侧的 coercer 收紧到与目录侧 `_finite_number` **逐字同一条规则**（不再收 `"120"` 这种字串、不再把 `90.5` 截成 90）——两侧宽窄不一样，同一份 config.yaml 就会让设置页显示 60 而 cron 按 120 删文件，正是「不夹取」那一条要防的事；判例现在拿同一个文件同时喂两侧。顺手把 `screenpipe_retention_days` 的 zh help 句里那个写死的「一小时」改成指向新旋钮（en 那半句本来就改了，zh 漏了 = 中文用户读到已经不成立的规则；§72.3 追记 + fixture 重铸）。

**没做（§72.5 写明）** — 不删备份、不 VACUUM、不按「盘快满了」自动改保留期、不给清理另起 launchd 任务、不动引擎自己的 `--retention-days`、不进首次运行向导、不为 `stale` 发系统通知（宪法第 10 条）。
