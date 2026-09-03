# Inventory + tokens schema (`schemaVersion: 1`, fields add-only)

Every adapter and every sensor speaks this. `testui_common.validate_inventory` checks the shape (`items[3].role` style paths) and a malformed reference is FAIL `reference_unreadable`, never pass.

```json
{
  "schemaVersion": 1,
  "producer": {"adapter": "web-source | web-playwright | project:extract_native_inventory | frozen-file", "mode": "runtime | source | frozen", "tool": "…", "skill": "test-ui 0.1.0", "argv": []},
  "side": {"role": "subject | reference", "kind": "dir | git | url | app | inventory | alias | design-system", "locator": "…", "resolved": "sha:… | path:… | url:… | sha256:…", "stack": "web-dom | swiftui | static-html", "commit": "…", "dirty": false,
           "seed": {"recipe": ["…"], "seeded_by_skill": true, "marker": {"path": "/api/health", "expr": ".demo == true"}}},
  "dims": {"themes": ["light", "dark"], "default_theme": "light", "viewports": [{"name": "desktop", "w": 1440, "h": 900}], "languages": ["zh", "en"], "scenes": ["initial"], "flags": ["default"]},
  "screens": [{"id": "board", "route": "", "source": ["web/src/pages/BoardPage.tsx"]}],
  "items": [{
    "id": "control:board:button:approve",
    "key": {"screen": "board", "role": "button", "slug": "approve"},
    "kind": "interactive | static | landmark | heading",
    "name": {"raw": "Approve", "zh": "批准", "en": "Approve", "alt": []}, "name_source": "text | aria-label | aria-labelledby | alt | label | title | none | L()",
    "pin": null, "owner": "web | shell | os | retired", "gated": false, "project_gated": true, "shortcut": "⌘↩", "count": 1, "dynamic": false,
    "topology": {"parent": "window>main:main>list:proposals", "order": 2, "side": null},
    "states": {"source": {"visible": true, "hidden_by": null, "focusable": true},
               "light::desktop::zh::rest": {"visible": true, "hidden_by": null, "focusable": true, "tab_index": null, "bbox": [812, 140, 64, 28],
                                            "computed": {"color": "rgb(255, 255, 255)", "background-color": "rgb(18, 117, 140)", "font": {"weight": 400, "size": 11, "line": 18, "family": "sans"}, "border-radius": 6, "padding": [0, 8]},
                                            "contrast": {"ratio": 5.9, "against": "#12758cff", "large": false}}},
    "screen": "board", "source": {"file": "web/src/components/board/CardActions.tsx", "line": 41}, "evidence": "runtime | source | frozen", "level": null
  }],
  "landmarks": [{"id": "landmark:board:navigation:rail", "role": "navigation", "topology": {"parent": "window", "order": 0, "side": "left"}, "bbox": [0, 0, 200, 800], "children_order": ["control:board:link:workbench"]}],
  "focus_walk": {"board::light::desktop::zh::rest": ["control:board:button:approve"]},
  "overflow": {"board::light::narrow::zh::rest": {"scrollWidth": 960, "clientWidth": 960}},
  "shots": [{"id": "shot:board:initial:light:desktop:zh", "path": "…/shots/board__initial__light__desktop__zh.png", "sha256": "…", "masks": [], "masked_ratio": 0.0}],
  "names": ["Approve", "批准"], "lang": "zh"
}
```

Rules: roles are WAI-ARIA names (`testui_common.ALL_ROLES`); the id grammar is the parity contract's (`<kind>:<screen>:<role>:<slug>[#n]`, `slugify` = lowercase, non-alphanumerics (CJK kept) → `-`, ≤ 48); the pairing key is `(screen family, role, slug)`; `states` keys are `theme::viewport::language::state` at runtime and `source` / `frozen` otherwise; `topology.side ∈ {left, right, top, bottom, inside, null}`; `items[].dynamic` marks runtime-named items (never paired). Frozen native items keep their native id (`control:about:label:about`) and carry the mapped ARIA role in `key.role` so the ledgers stay in the project's grammar.

## Tokens document

```json
{"schemaVersion": 1, "producer": {"adapter": "tokens-css | design-tokens-json", "mode": "source | frozen"},
 "default_theme": {"declared": {"mode": "system | fixed", "fallback": "light", "evidence": ["tokens.css: color-scheme: light"]}, "observed": null},
 "themes": {"light": {"color.bg": {"$type": "color", "$value": "#fafbfcff", "source": "web/src/styles/tokens.css:27", "var": "--bg"},
                      "typography.card-title-lg": {"$type": "typography", "$value": {"weight": 600, "size": 15.0, "line": 1.4, "family": "sans"}},
                      "layout.lane.width": {"$type": "dimension", "$value": "400px"}}, "dark": {}},
 "families": {"color": 49, "typography": 28}, "geometry": {}, "literals_outside": [{"file": "web/src/styles/board.css", "line": 88, "property": "border-radius", "value": "6px", "family": "radius"}],
 "type_scale": {}}
```

Colors canonical `#rrggbbaa`; lengths in px (`rem/em` × 16); weights regular/medium/semibold/bold → 400/500/600/700; a token in one theme and not the other is a `tokens.theme_parity` hit; MISSING tokens are reported only for `token_required_families` (default `layout`).

`gated` = behind a feature flag (subject side: `{flags.x && …}` / `data-gated`; runtime: present only with all flags on). `project_gated` (reference side, add-only) = the project's own inventory judges this id (`native-inventory.json` `gated`); `false` = listed, not judged (copy / help text) → the pairing marks it N-A. Same word in the native JSON, different meaning — kept apart on purpose.
