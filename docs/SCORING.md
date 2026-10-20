# Hydro Scoring and Ranking

How `hhsf score hydro` (or `make score`) turns a finished hydro run into a ranked shortlist, and why it ranks the way it does. The numbers it scores (head, firm flow, firm power) are explained in [ASSUMPTIONS.md](ASSUMPTIONS.md). The judgment calls behind the rules are in [DECISIONS.md](DECISIONS.md) (D-01, D-09, D-11). Every breakpoint and weight lives in [`config/scoring_profiles/hydro_v1.yaml`](../config/scoring_profiles/hydro_v1.yaml).

## The short version

1. A corridor needs at least **1 kW** of firm power to make the list (D-01). Corridors below that are filtered out, not given a low score.
2. Corridors on the list get a **hydro score from 0 to 100**: 70% from power and 30% from net head (D-11).
3. Corridors with **no flow estimate because the basin is too small** (D-09) stay on the list, ranked on head only and labeled `HEAD ONLY`. Since the regional yield (hydro run 30) almost every small basin has a low-confidence flow instead, so in Sequatchie this tier is empty.
4. The list is sorted in **confidence tiers**: every medium-confidence corridor first, then every low-confidence one, then the head-only ones. Within a tier, the higher score goes first.

## Where this departs from the original FR-3

The SRS's FR-3 weights head 40%, watershed area 30%, stream length 15% and rainfall 15%. The engineering plan's own review ([site-selection.md §2.A](../site-selection.md)) replaces that table: watershed area and rainfall are both stand-ins for flow (so flow gets counted two or three times), stream length doesn't make power, and rainfall barely varies within one county (D-10). The score is built on **firm power and net head** instead (§3.1).

Section 3 of the plan says to keep the FR-3 table as a labeled fallback ("screening") profile. **That isn't built yet**; see [Not built yet](#not-built-yet).

## Sub-scores

Both sub-scores are **absolute**: straight lines between fixed points, flat beyond the first and last. A corridor's score depends only on its own numbers, so adding or removing other corridors never changes it (site-selection.md A5).

| Sub-score | Input | Breakpoints | Examples |
| --- | --- | --- | --- |
| Power | `firm_power_w` (watts) | 1,000 W → 50, 3,000 W → 100 | 1 kW = 50, 2 kW = 75, 3 kW or more = 100 |
| Head | net head in feet (`net_head_m` × 3.28084) | 10 ft → 0, 200 ft → 100 | 10 ft or less = 0, 105 ft = 50, 200 ft or more = 100 |

- Power starts at 50, not 0, because anything that reaches the target already meets the household's needs. Above that, more power still earns more, up to 3 kW.
- Head uses **net** head (gross × 0.85, D-07), the head a turbine actually sees.
- Head counts on its own (30%) because a high-head site is usable even when the flow estimate is uncertain, and needs less water and smaller pipe for the same power.

```
hydro_score = 0.7 × power_subscore + 0.3 × head_subscore
```

Example: 2,000 W over 105 ft of net head gives 0.7 × 75 + 0.3 × 50 = **67.5**.

## What happens to each corridor

Each corridor gets exactly one `status` in `hydro_scores`:

| Status | When | Ranked? | Score |
| --- | --- | --- | --- |
| `scored` | Firm power ≥ 1 kW | Yes, in tier `medium` or `low` | Hydro score from power and head |
| `head_only` | No flow estimate because the basin is below the regression's 1.3 sq mi minimum and neither the gauge nor a regional yield covers it (`flow_confidence = below_applicability`), and net head ≥ 10 ft | Yes, in tier `head_only` | No hydro score; ranked on the head sub-score |
| `below_power_target` | Firm power < 1 kW | No | — |
| `below_min_head` | Would be head-only, but net head < 10 ft (too low for a practical small turbine) | No | — |
| `not_scorable` | Head missing, or flow missing for another reason (e.g. no drainage area) | No | — |

Filtered corridors are still saved, with a sentence saying why, so you can always answer "why isn't this stream on the list?".

**Head-only corridors** have no hydro score on purpose. Their head sub-score can't be compared with a hydro score (it says nothing about water), and they haven't been checked against the 1 kW target. Their explanation starts with `HEAD ONLY`. They need a flow check on the visit. For scale: with the conversions in ASSUMPTIONS.md, 1 kW needs net head (ft) × Q90 (cfs) of about 47 (e.g. 0.25 cfs needs about 190 ft), so a basin under 1.3 sq mi needs a lot of head *and* a strong spring to qualify.

## How confidence affects ranking

**Rule: tiers first, score second.** Every corridor in an earlier tier ranks above every corridor in a later one, however the scores compare:

| Tier | Contains | Why it's here |
| --- | --- | --- |
| 1. `medium` | Power from a StreamStats Q90 with every basin characteristic inside the equation's limits | The best flow estimate we have (still about ±60%) |
| 2. `low` | Power from any other flow: the Whitwell gauge transfer, the regional yield for small basins, StreamStats with an out-of-range characteristic, or the NHDPlus mean-flow fallback | The gauge transfer and mean flow usually **overstate** firm flow: by about 10× StreamStats on the streams checked, and over 100×. The regional yield is StreamStats' own typical yield extrapolated to small basins, so it isn't biased high, but it misses springs (ASSUMPTIONS.md) |
| 3. `head_only` | No flow estimate (D-09) | Nothing to say about power |

Within a tier: higher hydro score first (head sub-score for head-only), then more net head, then the lower `corridor_id`, so the order never changes between identical runs.

**Why tiers and not a confidence penalty.** A penalty (say, low-confidence scores × 0.7) would still let a big low-confidence number jump over a modest medium one, and the size of the penalty would be a made-up number. Low-confidence flows here aren't just noisier, they're biased high, so their scores tend to look *better*. Tiers make the rule easy to state and impossible to break by accident. The score itself is untouched, so a low-confidence corridor still shows its full score; it just can't jump the queue.

**What this means for the visit list.** Nothing is dropped for being low confidence. Read the list tier by tier: medium-confidence sites are the most likely to be real; low-confidence sites passed the 1 kW bar on a flow that is probably too high, so they need a dry-season flow check; head-only sites need both.

`rank` numbers the whole list (1 = best); `tier_rank` numbers each tier on its own.

## The explanation

Every `hydro_scores` row has an `explanation` (JSON) with the profile version, status, tier, a one-line summary, and one entry per factor:

```json
{
  "profile": "hydro_v1",
  "status": "scored",
  "tier": "medium",
  "summary": "Hydro score 67.5 = power 75.0 x 0.7 + head 50.0 x 0.3. Medium confidence (flow from StreamStats Q90).",
  "factors": [
    {"factor": "power", "input": "firm_power_w", "value": 2000, "breakpoints": [[1000, 50], [3000, 100]],
     "subscore": 75, "weight": 0.7, "contribution": 52.5,
     "reason": "2,000 W firm power: between 1000 (50) and 3000 (100), linear -> 75.0"},
    {"factor": "head", "input": "net_head_ft", "value": 105, "breakpoints": [[10, 0], [200, 100]],
     "subscore": 50, "weight": 0.3, "contribution": 15,
     "reason": "105 ft net head: between 10 (0) and 200 (100), linear -> 50.0"}
  ]
}
```

The contributions add up to the hydro score, so "why does A beat B?" is answered by comparing two explanations.

## Runs and reproducibility

- Each `hhsf score hydro` writes a new `analysis_runs` row with `analysis = 'hydro_score'`, `scoring_profile_version` (e.g. `hydro_v1`), the whole profile in `params`, and `source_run_id` pointing at the hydro run it scored. Then it writes one `hydro_scores` row per corridor of that hydro run.
- Earlier score runs are never changed. Scoring the same hydro run with two profiles gives two score runs side by side.
- **Never edit a profile that's been used.** Copy it to `hydro_v2.yaml`, change the `version` inside it, and run `uv run hhsf score hydro --profile hydro_v2`. The tool refuses a file whose `version` doesn't match its name.
- By default the AOI's latest finished hydro run is scored; `--run-id N` picks another.

To read the latest ranked list:

```sql
SELECT s.rank, s.tier, s.hydro_score, s.net_head_ft, c.firm_power_w, s.explanation->>'summary'
FROM hydro_scores s JOIN hydro_corridors c USING (corridor_id)
WHERE s.run_id = (SELECT max(run_id) FROM analysis_runs
                  WHERE analysis = 'hydro_score' AND status = 'finished')
  AND s.rank IS NOT NULL
ORDER BY s.rank;
```

## First results (Sequatchie, hydro run 1, profile hydro_v1, 2026-10-19)

| | Corridors |
| --- | --- |
| Ranked, medium confidence | 14 |
| Ranked, low confidence | 52 |
| Ranked, head only (D-09) | 283 |
| Filtered: below 1 kW | 626 |
| Filtered: head-only under 10 ft | 13 |
| Filtered: missing data | 1 |

Head-only corridors make up most of the ranked list (283 of 349), which is the D-09 "large share" case: a better small-basin flow estimate (for example a Collins River gauge) would help most. The top of the medium tier was reaches with over 800 ft of whole-reach head, the D-08 limitation fixed below.

## With the steepest stretch (hydro run 22, profile hydro_v1, 2026-10-19)

Each corridor is now the reach's steepest 1,000 ft stretch (D-08, [ASSUMPTIONS.md](ASSUMPTIONS.md#how-the-steepest-stretch-is-found)), so head and power are what one penstock could capture:

| | Corridors |
| --- | --- |
| Ranked, medium confidence | 1 |
| Ranked, low confidence | 18 |
| Ranked, head only (D-09) | 272 |
| Filtered: below 1 kW | 673 |
| Filtered: head-only under 10 ft | 24 |
| Filtered: missing data | 1 |

Only one StreamStats-backed stream clears 1 kW over 1,000 ft of pipe: Rogers Branch, 287 ft of net head for about 1.3 kW. With a 2,000 ft penstock the medium tier grows to 3.

The low tier is two different groups:

- **13 stretches on larger Collins River side creeks** (Conner, North Suck, Cain, Soddy and others) with 8–43 ft of head and 1–21 kW. Their flow is the NHDPlus *mean annual* flow, because StreamStats outlined the wrong basin for them (`streamstats_drainage_area_mismatch`) and they're outside the Whitwell gauge's basin. Mean flow runs about 130× StreamStats' Q90 here, so these power numbers are far too high; most would likely fall under 1 kW on a real dry-season flow. (Fixed in the next section: they're Walden Ridge creeks draining to the Tennessee River, not Collins River ones, and a retried StreamStats point gives all of them a Q90.)
- **5 plateau-edge streams** with 247–414 ft of head and 1.1–1.5 kW, from the Whitwell gauge transfer (about 10× StreamStats, an upper bound).

The breakpoints (D-11) were set before D-08 existed and may need another look now that heads are smaller.

## With StreamStats retried up the reach (hydro run 28, profile hydro_v1, 2026-10-19)

When StreamStats outlines the wrong basin at a reach's outlet, the tool now asks again further up the reach and scales the answer to the outlet ([ASSUMPTIONS.md](ASSUMPTIONS.md#when-streamstats-snaps-to-the-wrong-stream)). That gave a StreamStats Q90 to all 18 reaches that had been on the NHDPlus mean-flow fallback, so **no corridor uses mean flow any more** (unless StreamStats is down). On Q90 the 13 that had made the low tier produce 7–159 W, so they're filtered out below 1 kW. The scoring profile is unchanged: with no mean-flow corridors left there was no reason to rank them in a tier of their own.

| | Corridors |
| --- | --- |
| Ranked, medium confidence | 1 |
| Ranked, low confidence | 5 |
| Ranked, head only (D-09) | 272 |
| Filtered: below 1 kW | 686 |
| Filtered: head-only under 10 ft | 24 |
| Filtered: missing data | 1 |

The top 20:

| Rank | Tier | Score | Net head (ft) | Power (W) | Stream |
| --- | --- | --- | --- | --- | --- |
| 1 | medium | 71.0 | 287 | 1,345 | Rogers Branch |
| 2 | low | 73.6 | 331 | 1,489 | Stillhouse Branch |
| 3 | low | 69.6 | 414 | 1,262 | unnamed (NHDPlus 25000300018275) |
| 4 | low | 66.9 | 247 | 1,109 | Dry Branch |
| 5 | low | 66.8 | 409 | 1,105 | unnamed (NHDPlus 25000300000272) |
| 6 | low | 66.3 | 408 | 1,075 | Cordell Lane Branch |
| 7–20 | head only | — | 424 – 240 | — | 13 unnamed streams and Panther Branch |

The low tier is now only the five plateau-edge streams on the Whitwell gauge transfer (an upper bound, about 10× StreamStats), so ranks 7–20 are head-only streams with no flow estimate. Penstock sensitivity (D-08): 1 / 6 / 19 corridors reach 1 kW at 500 / 1,000 / 2,000 ft, 0 / 1 / 3 of them at medium confidence.

## With the regional yield for small basins (hydro run 30, profile hydro_v1, 2026-10-19)

Basins too small for StreamStats and outside the Whitwell gauge's basin now get the median StreamStats Q90 per square mile of their HUC8 ([ASSUMPTIONS.md](ASSUMPTIONS.md#regional-low-flow-yield), D-09), so they are scored on power instead of head. All 296 got a value; the best gives 56 W, so all of them are filtered out below 1 kW. **No corridor is head-only any more.**

| | Corridors |
| --- | --- |
| Ranked, medium confidence | 1 |
| Ranked, low confidence | 5 |
| Ranked, head only (D-09) | **0** (was 272) |
| Filtered: below 1 kW | 982 |
| Filtered: head-only under 10 ft | 0 (was 24) |
| Filtered: missing data | 1 |

The top 20 is now the six corridors that reach 1 kW; ranks 7–20 (the head-only streams in the previous section) are gone:

| Rank | Tier | Score | Net head (ft) | Power (W) | Stream | Flow |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | medium | 71.0 | 287 | 1,345 | Rogers Branch | StreamStats Q90 |
| 2 | low | 73.6 | 331 | 1,489 | Stillhouse Branch | Whitwell gauge transfer |
| 3 | low | 69.6 | 414 | 1,262 | unnamed (NHDPlus 25000300018275) | Whitwell gauge transfer |
| 4 | low | 66.9 | 247 | 1,109 | Dry Branch | Whitwell gauge transfer |
| 5 | low | 66.8 | 409 | 1,105 | unnamed (NHDPlus 25000300000272) | Whitwell gauge transfer |
| 6 | low | 66.3 | 408 | 1,075 | Cordell Lane Branch | Whitwell gauge transfer |

The five low-tier sites are small basins (0.65–1.1 sq mi) on the gauge transfer, which gives about 10× the regional yield for the same area. On the regional yield they'd make roughly 100–150 W; that's the D-09 open call. To see the old head-only ranking of small streams (for spring-hunting on a visit), re-run with `uv run hhsf analyze hydro --no-regional-yield` and score that run.

## Not built yet

- **FR-3 fallback profile.** §3 of the plan keeps the original weight table (head 40, watershed area 30, length 15, rainfall 15) as a labeled screening profile. It needs its own breakpoints for watershed area and length, and rainfall data (D-10).
- **Sensitivity check.** DECISIONS.md asks how much the top 20 changes under 70/30 vs 50/50 vs 100/0 weights. Each alternative is just a new profile file and a re-score.
- Scores aren't drawn on the map yet. The tile server publishes the latest score run as `latest_hydro_scores` (with `tier`, `rank` and `status`), ready for the map to color by tier. `make export` writes them to CSV, GeoJSON and a summary report in `data/outputs/`.
