_Auto-updated by `.github/workflows/insights.yml` — last run 2026-09-02 12:00 UTC, window: last 30 days. Aggregates only; no raw rows leave the database._

## Insights (AI-generated — concrete fixes from the views below)

- fix one
- fix two

> _Caveat: these aggregates commingle the anonymous devices of **every** install. A single shared / multi-user deployment counts as many devices and can skew the funnel and retention rates below. A per-tenant marker to separate installs is deferred (privacy-sensitive)._

**Totals:** 22 events from 5 devices.

### 1. Activation funnel

_Distinct devices reaching each lifecycle stage; later stages imply the earlier ones. Drop-off is the loss from the stage above._

| stage | devices | % of install | step drop-off |
|---|---|---|---|
| Installed (app launched) | 5 | 100.0% | — |
| Configured an ingest source | 5 | 100.0% | 0.0% |
| First proposal card | 3 | 60.0% | 40.0% |
| First approval | 2 | 40.0% | 33.3% |
| First delivery (dispatch) | 2 | 40.0% | 0.0% |

### 2. Reliability

**Ingest paths** — scans vs. skips per source (top skip reason surfaces `no_credentials` and friends):

| source | scans | skips | skip rate | top skip reason |
|---|---|---|---|---|
| (unknown) | 0 | 1 | 100.0% | — |
| gmail | 1 | 3 | 75.0% | no_credentials (2) |

**Dispatch:** 2 ok / 1 failed (33.3% of 3 attempts).

**Other action paths** (events carrying an `ok` flag):

| event | failures | total | rate |
|---|---|---|---|
| ask_answered | 0 | 1 | 0.0% |
| dispatch_failed | 1 | 1 | 100.0% |
| inbox_approve | 0 | 1 | 0.0% |
| resume_launch | 1 | 2 | 50.0% |

### 3. Feature abandonment

- Configured an ingest source but never reached a first card: **2 of 3** devices (66.7%).

**Used exactly once** — devices that touched a path a single time (tried-then-dropped candidates):

| event | devices used once |
|---|---|
| dispatch | 2 |
| inbox_approve | 1 |
| radar_scan | 1 |
| dispatch_failed | 1 |
| ask_answered | 1 |

### 4. Retention

_Of 5 distinct devices, the fraction that came back after their first-seen day (by client event time). Cohort = devices whose first day is old enough to have had the chance to return._

| window | cohort | returned | rate |
|---|---|---|---|
| day-2 (returned after their first day) | 5 | 3 | 60.0% |
| day-7 (still active a week or more later) | 4 | 2 | 50.0% |

<details>
<summary>Appendix — raw aggregate tables</summary>

### Events
| event | count |
|---|---|
| feature_first_reach | 5 |
| radar_skip | 4 |
| card_sent | 3 |
| dispatch | 2 |
| resume_launch | 2 |
| milestone_first_card | 1 |
| inbox_approve | 1 |
| radar_scan | 1 |
| dispatch_failed | 1 |
| ask_answered | 1 |
| orphan | 1 |

### Daily volume
| day | events |
|---|---|
| 2026-08-03 | 1 |
| 2026-08-13 | 1 |
| 2026-08-14 | 1 |
| 2026-08-15 | 1 |
| 2026-08-16 | 1 |
| 2026-08-17 | 1 |
| 2026-08-18 | 2 |
| 2026-08-19 | 2 |
| 2026-08-20 | 1 |
| 2026-08-21 | 1 |
| 2026-08-22 | 1 |
| 2026-08-23 | 1 |
| 2026-08-24 | 2 |
| 2026-08-25 | 1 |
| 2026-08-26 | 1 |
| 2026-08-31 | 1 |
| 2026-09-01 | 2 |

### App versions
| version | events |
|---|---|
| 1.0.0 | 20 |
| (unset) | 2 |

### Levels
| level | events |
|---|---|
| detailed | 1 |
| basic | 1 |

### Error rates (events carrying an ok flag)
| event | failures | total | rate |
|---|---|---|---|
| ask_answered | 0 | 1 | 0.0% |
| dispatch_failed | 1 | 1 | 100.0% |
| inbox_approve | 0 | 1 | 0.0% |
| resume_launch | 1 | 2 | 50.0% |

</details>