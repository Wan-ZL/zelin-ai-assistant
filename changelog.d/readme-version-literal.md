type: fixed
- README no longer carries a hand-written version literal; the git tag on `main` is the only version truth (§56.1). The literal red-lit its own release: `release.yml` fetches tags, so the README audit saw the two newest tags while the page still named the previous one and the Release job died at the unit-test step. A tag-independent test (`test_no_version_literals`) now pins it.
