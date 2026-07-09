# docs/assets — demo & screenshot conventions

The READMEs embed assets from this directory **by fixed filename** — drop a new file in under the
same name and every embed updates with zero markdown edits. Shot compositions come from the shot
list and storyboard in [docs/DEMO.md](../DEMO.md).

## Inventory

| File | Status | Content | Spec |
|---|---|---|---|
| `kanban.png` | ✅ | hero shot: kanban main window, five lanes populated (scene `initial`) | 2x Retina PNG, light mode, window ≈1280 pt wide (≈2560 px image) |
| `popover.png` | ✅ | menu-bar popover: badge count, quick-capture field, collapsed cards (DEMO shot 1) | 2x Retina PNG, light mode |
| `flow.gif` | ✅ | the life of one card: approve → queued → executing → review → accepted | **hard budget < 5 MB**; ≈800–1000 px wide, 10–15 fps; `demo.mp4` is the full-quality source |
| `demo.mp4` | ✅ | 30-second storyboard (DEMO.md) | 1080p+; to play inline in the README use a GitHub *user-attachments* URL — bare `<video>` tags don't render on GitHub |
| `t2-card.png` | slot | expanded T2 card: $85 cost, typed confirmation, disagreement, repeated ×3 (DEMO shot 3) | 2x PNG; fills README marker `<!-- screenshot slot: docs/assets/t2-card.png -->` |
| `review-final-draft.png` | slot | review lane, chat delivery with FINAL DRAFT expanded next to a repo-delivery card (DEMO shot 4) | 2x PNG; fills README marker `<!-- screenshot slot: docs/assets/review-final-draft.png -->` |
| `trash.png` | slot | recycle bin expanded: restore / keep-forever buttons + search (DEMO shot 5) | 2x PNG |

## Capture rules

- **All visible data must be fictional.** Run `python3 scripts/demo_seed.py /tmp/assistant-demo`
  first and point the app at it (exact commands in [docs/DEMO.md](../DEMO.md)); re-run the seeder
  right before each shot so the freshness banner doesn't go orange (`generated_at` > 90 s).
- Light mode, 2x Retina display; capture the window with its shadow (⌘⇧4, then Space).
- Filenames above are load-bearing — never rename an embedded asset without updating both
  `README.md` and `README.zh-CN.md` in the same commit.

## Social preview (the card GitHub shows on X/Slack/HN)

- **1280 × 640 px PNG** (2:1). GitHub has no API for this — upload it manually under repo
  **Settings → General → Social preview**. This manual step belongs on the release checklist.
- Suggested content: app icon + the English tagline ("You approve and accept — everything else is
  automated.") over a low-opacity kanban screenshot; keep icon and text inside the central
  ≈1200 × 600 safe area since some platforms crop the edges.
- Keep the source file here as `social-preview.png` for provenance, even though GitHub doesn't
  read it from the repo.
