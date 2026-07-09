# Example: Good Raw File Excerpt

This is a reference example showing how to correctly process screenpipe audio + OCR into a raw file. **All names, companies, projects, numbers, and links below are fictional** — the structure (cleaned meeting transcript with speaker attribution, scene-by-scene establishing shots, dwell time, `[DELTA]` markers, deduped frames, verbatim Conversations Captured section) is what you should copy.

---

## Meeting Transcript

Discord voice call about inkweld PR #42 (cleaned audio, chronological; consecutive 30s segments merged):

**[20:05:14] [[Alex Doe]]**: The `--drafts` flag filters at scan time, so the build cache never even sees draft pages — that's why the diff is so small.

**[20:06:02] Zelin**: 有个 edge case — draft 从 true 改成 false 的时候，cache 里根本没有这一页，得强制 rebuild。你现在 handle 了吗？

**[20:06:35] [[Alex Doe]]**: Not yet — good catch. I'll push a test for that tonight.

**[20:07:48] (anon, likely a community member who joined the voice channel)**: Are you two talking about the drafts PR? Can you post the benchmark numbers in #general after?

**[20:08:15] Zelin**: Sure，一会儿贴。

---

## Screen Activity

### Scene 1: Obsidian — Inkweld wiki page
**[20:15:04]** First frame. Full content:

Obsidian window showing `4 - wiki / Inkweld`, editing view. Visible content:

**Properties**: type=project, tags=[oss, static-site, hobby], sources listing 3 raw files.

**Template engine 对比表**:
| Engine | Build time (1,000 pages) | Notes |
|---|---|---|
| stencil-js | 4.0 s | current default |
| quickmustache | 2.5 s | 不支持 partials |
| handroll | 6.0 s | syntax 最丰富 |

**Open Questions (v0.3)** (3 items visible):
1. incremental build 的 cache key 用 mtime 还是 content hash？
2. 要不要在 v0.3 直接 drop Node 18 support？
3. theme system 先做 CSS variables 还是完整 template override？

[20:15:12 – 20:15:40] (7 frames, still viewing Inkweld page, no content change)

---

### Scene 2: Safari — inkweld docs local preview
**[20:16:02]** First frame (start of a ~3 min reading session):

Safari at `http://localhost:8000/guide/getting-started/` — the docs site inkweld generates for itself. Sidebar nav: Getting Started / Configuration / Templates / Deploy. Body visible:

> **Quickstart**
> ```
> $ inkweld new my-site
> $ cd my-site && inkweld serve
> ```
> Your site is now live at localhost:8000 with hot reload.

**[20:17:15]** [DELTA] Scrolled down — new section now visible: "Front matter". Code block:

> ```toml
> title = "Hello Inkweld"
> date = 2026-02-02
> draft = false
> ```

[20:17:20 – 20:19:05] (still reading Front matter section, no content change)

---

### Scene 3: Terminal — build + benchmark session
**[20:19:30]** First frame. Terminal in `~/Code/inkweld` (main branch). Output visible:

> $ inkweld build --profile
> inkweld v0.3.0-dev
> Scanning content/ ... 120 pages, 30 assets
> Rendering ......... done in 3.2 s
> Writing dist/ ..... done
> Total: 4.0 s (cache: cold)

**[20:20:05]** [DELTA] New command output appeared below:

> $ example-bench run --suite ssg-small
> example-bench 0.2.0
> suite: ssg-small (10 sites × 100 pages)
> inkweld      mean 4.0 s   p95 4.4 s
> staticgen-x  mean 5.0 s   p95 5.6 s
> sitepress    mean 3.5 s   p95 3.9 s

[20:20:08 – 20:21:12] (still viewing benchmark output, no content change)

---

### Scene 4: Discord — alex.doe DM
**[20:22:41]** First frame. Full conversation visible (2 sec glance — checking for a reply, not composing):

> **alex.doe** Today at 7:45 PM:
> opened PR #42 on inkweld — adds a `--drafts` flag to `inkweld serve`. Tests pass locally, mind taking a look when you get a chance?
> [1 reaction: thumbsup]
>
> **Zelin** Today at 7:52 PM:
> Nice, will review tonight. 顺手把 changelog 也补一条吧
>
> **alex.doe** Today at 8:12 PM:
> just pushed the edge-case test we talked about on the call — draft flipping true→false now forces a rebuild
> [1 reply in thread, 10 minutes ago]
> [alex.doe is typing…]

[20:22:43] Switched back to Terminal

---

### Scene 5: Assistant chat — TOML date question
**[20:24:10]** First frame (viewing a completed answer):

Assistant chat window, conversation visible in full:

> **Zelin**: TOML 的 date 是不是 first-class type？frontmatter 里写 date = 2026-02-02 不加引号，parser 会直接给我 date object 吗？
>
> **Assistant**: 对。TOML 规范里 date/datetime 是原生类型（RFC 3339 格式），不加引号就会被 parse 成 date 对象；YAML 则依赖各 parser 的隐式类型推断，行为不统一，所以很多工具要求加引号。

[20:24:14 – 20:25:30] (still viewing answer, no content change)

---

### Scene 6: Firefox — SSG forum thread
**[20:27:33]** First frame. Thread "Incremental builds: mtime or content hash?" at `https://forum.ssg-builders.example.org/t/incremental-builds/240`. Posts visible:

> **sam.roe** Today 11:20 AM: mtime breaks the moment you clone a repo fresh — every file looks modified. Content hash or nothing. [12 upvotes] [4 replies, last 2 hours ago]
> **casey.kim** Today 1:45 PM: Middle ground: use mtime as a fast pre-check, only hash when mtime changed. Best of both. [8 upvotes]
> **forum-bot** PINNED Today 9:00 AM: February showcase thread is open — post what you built!

Thread tags: build-performance, caching. (Relates to Open Question 1 on the Inkweld wiki page — Zelin is researching the cache-key decision.)

[20:27:40 – 20:28:55] (still reading thread, no new posts)

---

## Conversations Captured

### Alex Doe — Discord DM (glanced at 20:22:41)

**alex.doe** (Today 7:45 PM):
opened PR #42 on inkweld — adds a `--drafts` flag to `inkweld serve`. Tests pass locally, mind taking a look when you get a chance?

**Zelin** (Today 7:52 PM):
Nice, will review tonight. 顺手把 changelog 也补一条吧

**alex.doe** (Today 8:12 PM):
just pushed the edge-case test we talked about on the call — draft flipping true→false now forces a rebuild

---

### Assistant Chat — TOML date type Q&A (visible at 20:24:10)

**Zelin**: TOML 的 date 是不是 first-class type？frontmatter 里写 date = 2026-02-02 不加引号，parser 会直接给我 date object 吗？

**Assistant**: 对。TOML 规范里 date/datetime 是原生类型（RFC 3339 格式），不加引号就会被 parse 成 date 对象；YAML 则依赖各 parser 的隐式类型推断，行为不统一，所以很多工具要求加引号。
