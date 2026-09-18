pr: `fix/web-schedule-refocus-layout-effect`（2026-09-17 全覆盖收尾，dev train）
phase: 横切（web；§61.7 日程块）
law: §61.7（行为不变：回车提交被壳 reject → 回滚并把焦点还给那个输入框；只改焦点归还的时机）

dev 上 `Web tests (build + vitest)` 在同一个 sha（38c41bd2）一次绿一次红，红的是 `RecordingSection.schedule.test.tsx` 的「回车提交的 reject 把焦点还给输入框」：`expected <body> to be <input>`。根因不是判例：回滚 = 两个 `<input type="time">` 重挂（key 变），带焦点的旧 input 被卸掉那一刻 `document.activeElement` 掉到 body，而把焦点放回去的是一个 **passive** `useEffect`——它在 commit 之后另起一拍才跑，判例的 `waitFor` 用 MutationObserver 在 commit 当口就看见了新值、紧接着同步断言焦点，撞见 body 的概率取决于调度（CI 上约 1/6）。

修法只动组件：refocus 改 `useLayoutEffect`，焦点在同一次 commit 里放回去（重挂 → 焦点回来，中间不闪一帧）。判例一字不改，本地 5/5 绿；`main` 上没重现只是运气（six 次全绿）。
