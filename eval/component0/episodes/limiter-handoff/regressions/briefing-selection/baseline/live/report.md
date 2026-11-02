# Component 0 run report: live-v2-contextd

Trials scored: 10. Agents: codex. Model: gpt-6.1-sol. Codex 0.159.3.
Baseline run: live-v1.

## Per arm (real gold)

| run | arm | n | solved | ttf median | ttf mean (reached) | repeated | forbidden | constraint | pkt recall | pkt precision | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| live-v2-contextd | none | 5 | 1.00 | 10.00 | 10.40 (5/5) | 0.20 | 0.00 | 0.00 | 0.00 | - | 213327 |
| live-v2-contextd | contextd | 5 | 0.00 | not reached | - (0/5) | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 55178 |
| live-v1 | none | 5 | 1.00 | 10.00 | 10.60 (5/5) | 0.00 | 0.00 | 0.00 | 0.00 | - | 215815 |
| live-v1 | raw | 5 | 1.00 | 3.00 | 3.40 (5/5) | 0.00 | 0.00 | 0.00 | 0.71 | 0.28 | 133621 |
| live-v1 | summary | 5 | 1.00 | 3.00 | 4.00 (5/5) | 0.20 | 0.00 | 0.00 | 0.14 | 0.75 | 185530 |
| live-v1 | oracle | 5 | 1.00 | 2.00 | 2.60 (5/5) | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 70495 |
| live-v1 | stale-only | 5 | 0.20 | 3.00 | 3.20 (5/5) | 2.40 | 1.00 | 0.20 | 0.29 | 0.60 | 646111 |

contextd ladder: observation recall 0.00, selection recall 0.00

ttf median counts not-reached as infinite; ttf mean is over reached trials only. Rates are shares of trials. Packet metrics are properties of the arm's packet.

## Within-run paired per-rep deltas vs none (arm minus none)

Each arm is paired with none from the same run. Bootstrap 95% CI of the mean over reps: seed 20260930, 10000 resamples. ttf deltas use reps where both trials reached the frontier.

| run | arm | metric | pairs | mean delta | 95% CI | per rep |
|---|---|---|---|---|---|---|
| live-v2-contextd | contextd | solved | 5 | -1.00 | [-1.00, -1.00] | -1.00 -1.00 -1.00 -1.00 -1.00 |
| live-v2-contextd | contextd | frontier_reached | 5 | -1.00 | [-1.00, -1.00] | -1.00 -1.00 -1.00 -1.00 -1.00 |
| live-v2-contextd | contextd | ttf_both_reached | 0 | - | - | - |
| live-v2-contextd | contextd | repeated_work_calls | 5 | -0.20 | [-0.60, 0.00] | 0.00 -1.00 0.00 0.00 0.00 |
| live-v2-contextd | contextd | forbidden_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v2-contextd | contextd | constraint_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v2-contextd | contextd | tokens | 5 | -158149.40 | [-175357.40, -141021.20] | -133189 -190416 -166785 -161603 -138754 |
| live-v1 | raw | solved | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | raw | frontier_reached | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | raw | ttf_both_reached | 5 | -7.20 | [-8.20, -6.20] | -8.00 -6.00 -9.00 -6.00 -7.00 |
| live-v1 | raw | repeated_work_calls | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | raw | forbidden_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | raw | constraint_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | raw | tokens | 5 | -82194.40 | [-104482.80, -56829.80] | -96974 -39956 -61322 -102959 -109761 |
| live-v1 | summary | solved | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | summary | frontier_reached | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | summary | ttf_both_reached | 5 | -6.60 | [-8.40, -4.80] | -8.00 -4.00 -9.00 -8.00 -4.00 |
| live-v1 | summary | repeated_work_calls | 5 | 0.20 | [0.00, 0.60] | 0.00 0.00 0.00 0.00 1.00 |
| live-v1 | summary | forbidden_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | summary | constraint_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | summary | tokens | 5 | -30285.40 | [-65253.20, 4682.40] | -246 7686 4143 -79991 -83019 |
| live-v1 | oracle | solved | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | oracle | frontier_reached | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | oracle | ttf_both_reached | 5 | -8.00 | [-8.00, -8.00] | -8.00 -8.00 -8.00 -8.00 -8.00 |
| live-v1 | oracle | repeated_work_calls | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | oracle | forbidden_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | oracle | constraint_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | oracle | tokens | 5 | -145320.00 | [-164893.80, -120995.40] | -154239 -98833 -138413 -161037 -174078 |
| live-v1 | stale-only | solved | 5 | -0.80 | [-1.00, -0.40] | -1.00 -1.00 -1.00 -1.00 0.00 |
| live-v1 | stale-only | frontier_reached | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| live-v1 | stale-only | ttf_both_reached | 5 | -7.40 | [-8.40, -6.40] | -8.00 -6.00 -9.00 -8.00 -6.00 |
| live-v1 | stale-only | repeated_work_calls | 5 | 2.40 | [1.60, 3.40] | 2.00 4.00 3.00 2.00 1.00 |
| live-v1 | stale-only | forbidden_any | 5 | 1.00 | [1.00, 1.00] | 1.00 1.00 1.00 1.00 1.00 |
| live-v1 | stale-only | constraint_any | 5 | 0.20 | [0.00, 0.60] | 0.00 0.00 0.00 0.00 1.00 |
| live-v1 | stale-only | tokens | 5 | 430295.40 | [320310.60, 553477.20] | 348572 360645 624218 546160 271882 |

## Cross-run paired deltas vs baseline none

Arm in this run (live-v2-contextd) minus none in the baseline run (live-v1), paired by rep. Within-run pairs above are the preferred comparison.

| arm | metric | pairs | mean delta | 95% CI | per rep |
|---|---|---|---|---|---|
| contextd | solved | 5 | -1.00 | [-1.00, -1.00] | -1.00 -1.00 -1.00 -1.00 -1.00 |
| contextd | frontier_reached | 5 | -1.00 | [-1.00, -1.00] | -1.00 -1.00 -1.00 -1.00 -1.00 |
| contextd | ttf_both_reached | 0 | - | - | - |
| contextd | repeated_work_calls | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| contextd | forbidden_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| contextd | constraint_any | 5 | 0.00 | [0.00, 0.00] | 0.00 0.00 0.00 0.00 0.00 |
| contextd | tokens | 5 | -160637.80 | [-174259.40, -143825.40] | -171239 -127692 -164812 -159140 -180306 |

## Failure tags

| run | arm | behaviour | ok | placement |
|---|---|---|---|---|
| live-v2-contextd | none | 1 | 4 | 0 |
| live-v2-contextd | contextd | 0 | 0 | 5 |
| live-v1 | none | 0 | 5 | 0 |
| live-v1 | raw | 0 | 0 | 5 |
| live-v1 | summary | 0 | 0 | 5 |
| live-v1 | oracle | 0 | 5 | 0 |
| live-v1 | stale-only | 0 | 0 | 5 |

## Predictions

Frozen packet `e44ea0cdb976e412b9e61d0a9620961b2632723110e53942f62efc167b9fcc95`.

| id | metric | comparison | direction | where | result |
|---|---|---|---|---|---|
| recall_below_summary | packet_required_recall | contextd vs summary | lower | cross-run | held |
| slower_than_oracle | ttf_median | contextd vs oracle | higher | cross-run | held |
| slower_than_raw | ttf_median | contextd vs raw | higher | cross-run | held |
| costlier_than_oracle | tokens_mean | contextd vs oracle | higher | cross-run | missed |
| safer_than_stale | forbidden_rate | contextd vs stale-only | lower | cross-run | held |

## G6: not applicable

G6: not applicable to this run

```

```
