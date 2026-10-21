# Hydro Scoring and Ranking

How `hhsf score hydro` (or `make score`) turns a finished hydro run into a ranked shortlist, and why it ranks the way it does. The numbers it scores (head, firm flow, firm power) are explained in [ASSUMPTIONS.md](ASSUMPTIONS.md). The judgment calls behind the rules are in [DECISIONS.md](DECISIONS.md) (D-01, D-09, D-11). Every breakpoint and weight lives in the scoring profile, [`config/scoring_profiles/hydro_v6.yaml`](../config/scoring_profiles/hydro_v6.yaml) by default. `hydro_v2` is `hydro_v1` plus the `low_flow` tier (D-09, decided 2026-10-19); `hydro_v3` is `hydro_v2` with the 10 ft net-head minimum applied to every tier (D-11, 2026-10-20); `hydro_v4` keeps `hydro_v3`'s hydro rules and adds the [overall score](#the-overall-score-fr-7) that ranks each tier (FR-7, D-15, 2026-10-21); `hydro_v5` adds cropland that's already open to its agriculture part (D-16, 2026-10-21); `hydro_v6` adds the privacy part (FR-6, D-17, 2026-10-21). Earlier score runs used the older profiles.

## The short version

1. A corridor needs at least **1 kW** of firm power and **10 ft** of net head to get a score (D-01, D-11).
2. Corridors that make it get a **hydro score from 0 to 100**: 70% from power and 30% from net head (D-11).
3. Corridors **under 1 kW on a low-confidence flow** stay on the list, last, labeled `LOW FLOW` and ranked by power (D-01, D-09): the flow behind them is too rough to rule them out. Under 1 kW on a medium-confidence StreamStats flow, a corridor is filtered out.
4. Corridors with **no flow estimate because the basin is too small** (D-09) stay on the list, ranked on head only and labeled `HEAD ONLY`. Since every small basin gets the regional yield, this tier is empty in Sequatchie unless a run uses `--no-regional-yield`.
5. The list is sorted in **confidence tiers**: medium-confidence, then low-confidence, then head-only, then low-flow. Within a tier, the higher **overall score** goes first: the hydro score plus how buildable, farmable and reachable the site is (FR-7, `hydro_v4`). Older profiles sort a tier by hydro score.

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
| `scored` | Firm power ≥ 1 kW, and net head ≥ 10 ft (`hydro_v3`) | Yes, in tier `medium` or `low` | Hydro score from power and head |
| `head_only` | No flow estimate because the basin is below the regression's 1.3 sq mi minimum and neither the regional yield nor the gauge covers it (`flow_confidence = below_applicability`), and net head ≥ 10 ft | Yes, in tier `head_only` | No hydro score; ranked on the head sub-score |
| `low_flow` | Firm power < 1 kW, but on a low-confidence flow (`power_confidence = low`), and net head ≥ 10 ft. Only with a profile that lists the `low_flow` tier (`hydro_v2` and later) | Yes, in tier `low_flow` | No hydro score; ranked by firm power |
| `below_power_target` | Firm power < 1 kW on a medium-confidence flow (or on any flow with `hydro_v1`) | No | — |
| `below_min_head` | Net head < 10 ft (too low for a practical small turbine): any corridor with `hydro_v3`; only would-be head-only or low-flow ones with `hydro_v1` and `hydro_v2` | No | — |
| `not_scorable` | Head missing, or flow missing for another reason (e.g. no drainage area) | No | — |

Filtered corridors are still saved, with a sentence saying why, so you can always answer "why isn't this stream on the list?".

**Head-only corridors** have no hydro score on purpose. Their head sub-score can't be compared with a hydro score (it says nothing about water), and they haven't been checked against the 1 kW target. Their explanation starts with `HEAD ONLY`. They need a flow check on the visit.

**Low-flow corridors** have no hydro score either: the power sub-score starts at the 1 kW target, so it has nothing to say below it. They are ordered by firm power, then net head. Their explanation starts with `LOW FLOW` and names the flow method. In Sequatchie they are almost all small basins on the regional yield, a typical-basin estimate that can't see springs, so they are the list to check for a spring with lots of head. For scale: with the conversions in ASSUMPTIONS.md, 1 kW needs net head (ft) × Q90 (cfs) of about 47 (e.g. 0.25 cfs needs about 190 ft), so a basin under 1.3 sq mi needs a lot of head *and* a strong spring to qualify.

## How confidence affects ranking

**Rule: tiers first, score second.** Every corridor in an earlier tier ranks above every corridor in a later one, however the scores compare:

| Tier | Contains | Why it's here |
| --- | --- | --- |
| 1. `medium` | Power from a StreamStats Q90 with every basin characteristic inside the equation's limits | The best flow estimate we have (still about ±60%) |
| 2. `low` | Power from any other flow: the Whitwell gauge transfer, the regional yield for small basins, StreamStats with an out-of-range characteristic, or the NHDPlus mean-flow fallback | The gauge transfer and mean flow usually **overstate** firm flow: by about 10× StreamStats on the streams checked, and over 100×. The regional yield is StreamStats' own typical yield extrapolated to small basins, so it isn't biased high, but it misses springs (ASSUMPTIONS.md) |
| 3. `head_only` | No flow estimate (D-09) | Nothing to say about power |
| 4. `low_flow` | Power under 1 kW on a low-confidence flow (D-01, D-09) | Probably can't make 1 kW, but the flow is too rough to say for sure |

Within a tier: higher overall score first (`hydro_v4`; with older profiles, higher hydro score, head sub-score for head-only, firm power for low flow), then more net head, then the lower `corridor_id`, so the order never changes between identical runs.

**Why tiers and not a confidence penalty.** A penalty (say, low-confidence scores × 0.7) would still let a big low-confidence number jump over a modest medium one, and the size of the penalty would be a made-up number. Low-confidence flows here aren't just noisier, they're biased high, so their scores tend to look *better*. Tiers make the rule easy to state and impossible to break by accident. The score itself is untouched, so a low-confidence corridor still shows its full score; it just can't jump the queue.

**What this means for the visit list.** Nothing is dropped for being low confidence. Read the list tier by tier: medium-confidence sites are the most likely to be real; low-confidence sites passed the 1 kW bar on a flow that is probably too high, so they need a dry-season flow check; head-only sites need both; low-flow sites only work if the visit finds much more water than the estimate (a spring).

`rank` numbers the whole list (1 = best); `tier_rank` numbers each tier on its own.

## The overall score (FR-7)

`hydro_v4` adds the overall score the requirements ask for (FR-7): Hydro 40%, Buildability 20%, Agriculture 20%, Privacy 10%, Logistics 10%. It doesn't change who makes the list (the 1 kW and 10 ft rules above) or the tiers; it orders the corridors **within** each tier. Every part is a 0–100 sub-score built like the hydro one: factors, each an absolute curve of `[input, score]` points (straight lines between them, flat beyond the ends), weighted within the part. All of it is in the `overall` section of the profile.

| Part (FR-7 weight) | Factor (weight in the part) | Curve `[input, score]` |
| --- | --- | --- |
| Hydro (40%) | The hydro score. Head-only corridors: their head sub-score. Low-flow corridors: the hydro formula with the power curve extended below the target (0 W → 0, 1 kW → 50) | as above |
| Buildability (20%) | Buildable acres on the turbine parcel (50%) | 0 → 0, ½ acre → 40, 5 acres → 100 |
| | Turbine end to the nearest buildable ground, ft (25%) | 0 → 100, ¼ mile → 50, 1 mile → 0; none → 0 |
| | Turbine end to the nearest public road, ft (25%) | 500 → 100, 1 mile → 0 |
| Agriculture (20%) | Prime farmland and farmland of statewide importance, acres (30%; 40% in `hydro_v4`) | 0 → 0, 5 → 70, 20 → 100 |
| | Cropland (capability class 1–3), acres (20%; 30% in `hydro_v4`) | 0 → 0, 10 → 60, 40 → 100 |
| | Pasture, AUM a year (20%; 30% in `hydro_v4`) | 0 → 0, 60 (5 cows) → 50, 240 (20 cows) → 100 |
| | Cropland that's open now (NLCD 2021 field, pasture or crops on class 1–3 soils), acres (30%; `hydro_v5` only, D-16) | 0 → 0, 5 → 60, 20 → 100 |
| Privacy (10%; `hydro_v6` on, D-17; left out before) | Other buildings within 1/4 mile of the home site (35%) | 0 → 100, 2 → 70, 5 → 40, 15 → 0 |
| | The nearest other building, ft (15%) | 200 → 0, 660 → 50, 1,320 → 80, 2,640 → 100; none within 2 miles → 100 |
| | Buildings per square mile within 1 mile (20%) | 5 → 100, 25 → 60, 100 → 0 |
| | Woods on the line toward the nearest public road, ft, over its first 1,320 ft (20%) | 0 → 0, 200 → 70, 500 → 100 |
| | The home site to the nearest public road, ft (10%) | 100 → 0, 500 → 50, 1,320 → 100 |
| Logistics (10%) | Drive from downtown Chattanooga, minutes | 30 → 100, 60 → 70, 120 → 0 |

The home site is the turbine parcel's buildable ground nearest the turbine end, or the turbine end where there's none; buildings are Microsoft footprints of 400 sq ft or more, not on the turbine parcel ([ASSUMPTIONS.md](ASSUMPTIONS.md#privacy)). The site numbers are the flags of PARTs 15–18, 20 and 21: privacy ([ASSUMPTIONS.md](ASSUMPTIONS.md#privacy)), buildable ground ([ASSUMPTIONS.md](ASSUMPTIONS.md#buildable-ground)), farmland ([ASSUMPTIONS.md](ASSUMPTIONS.md#farmland)), land cover ([ASSUMPTIONS.md](ASSUMPTIONS.md#land-cover)), the nearest public road and the drive time ([ASSUMPTIONS.md](ASSUMPTIONS.md#roads-and-drive-time)). They're read when `make score` runs, so run `make analyze` and `make drive-time` first, and score again after either.

**Missing data is left out, never scored as zero** (D-15). A factor with no data (no turbine parcel, a step not run, no route) is dropped and the part's other weights rescale; a part with nothing left is dropped and the other parts' weights rescale. Before `hydro_v6` privacy had no data at all, so the other four parts counted as 44%, 22%, 22% and 11% (owner, D-15); with it, all five parts count at their FR-7 weights wherever privacy was computed. A measured nothing is a zero, though: a parcel with no buildable ground or no farmland scores 0 on that factor. So a corridor with no turbine parcel (it ends outside the loaded counties) is scored on hydro, road access and drive time alone, and can look better than one whose parcel was checked and found wanting. 140 ranked corridors are in that position.

**Overall scores only compare within a tier.** A low-flow corridor's hydro part is small (its power is under the target), so its overall score is mostly land and access, and can be higher than a medium-confidence corridor's; it still ranks below it. The explanation's `overall` entry lists each part's sub-score, rescaled weight, contribution and factors, and what was left out; the summary ends with the formula, e.g. `Overall 54.8 = hydro 55.4 x 0.44 + buildability 61.4 x 0.22 + agriculture 48.5 x 0.22 + logistics 52.1 x 0.11 (privacy left out: no data).`

### What it did (five counties, profile hydro_v4, 2026-10-21)

The three Grundy streams change order: Big Creek (hydro 40, overall 58: 917 buildable acres and 923 acres of prime farmland on its parcel) goes from #3 to #1, ahead of Negro Den Creek (hydro 64, overall 57) and the Elk River (52). In Marion, Bethel Creek (overall 55) passes the unnamed stream (hydro 66, overall 43: no buildable ground or farmland on its 44-acre parcel). Rogers Branch, Sequatchie's only medium-confidence site, stays #1 at 47 (hydro 71; buildability 25, agriculture 7). The 4,913 low-flow corridors are almost all reordered: their order now follows the land more than their tiny power estimates. Across ranked corridors the median buildability is 71, agriculture 70 and logistics 55; 140 have no agriculture part (no turbine parcel) and 34 no logistics part (no drive time).

With `hydro_v5` (the same day, PART 20) the 1 kW order doesn't change: all six parcels are about 99% forest, so open cropland adds nothing to any of them (Big Creek 56, Negro Den Creek 52, Elk River 48, Bethel Creek 51, Marion's unnamed stream 43, Rogers Branch 47). The median agriculture part drops from 70 to 63, and low-flow corridors move an average of 44 places.

With `hydro_v6` (PART 21) privacy counts, and the 1 kW order still doesn't change: Big Creek 59 (privacy 85), Negro Den Creek 57 and the Elk River 53 (one parcel, privacy 100: nothing within 1/2 mile), Bethel Creek 56 (93), Marion's unnamed stream 44 (51: 4 buildings within 1/4 mile, 117 per square mile around it), Rogers Branch 47 (56: its home site is the turbine end, 640 ft from the nearest house). The low-flow tiers reorder (an average of 66 places): in Sequatchie, Kell Branch drops from #4 to #31 (privacy 27) and Easterly Branch rises from #9 to #7; in Marion an unnamed stream drops from #6 to #43 and Sulphur Branch rises from #7 to #5; Grundy's top 5 stay put and its #6–10 swap places. The median privacy part across ranked corridors is 72.

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
- **Never edit a profile that's been used.** Copy it to the next version (e.g. `hydro_v3.yaml`), change the `version` inside it, and run `uv run hhsf score hydro --profile hydro_v3`. To re-score with the old rules (low-flow corridors filtered out), use `--profile hydro_v1`. The tool refuses a file whose `version` doesn't match its name.
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

## With the regional yield and the low-flow tier (hydro run 36, profile hydro_v2, 2026-10-20)

D-09 was decided (DECISIONS.md): every basin too small for StreamStats now gets the median StreamStats Q90 per square mile of its HUC8, including the ones in the Whitwell gauge's basin, which used to get the gauge transfer, about 10× higher ([ASSUMPTIONS.md](ASSUMPTIONS.md#regional-low-flow-yield)). Corridors under 1 kW on such a low-confidence flow stay on the list in the `low_flow` tier. (An earlier draft, hydro run 30, kept the gauge transfer in its basin and filtered small basins below 1 kW: 6 ranked, 0 head only.)

| | Corridors |
| --- | --- |
| Ranked, medium confidence | 1 |
| Ranked, low confidence | 0 (was 5) |
| Ranked, head only (D-09) | 0 (was 272) |
| Ranked, low flow | 575 |
| Filtered: below 1 kW on a StreamStats Q90 | 349 |
| Filtered: head only or low flow under 10 ft | 63 |
| Filtered: missing data | 1 |

The top 20:

| Rank | Tier | Score | Net head (ft) | Power (W) | Stream |
| --- | --- | --- | --- | --- | --- |
| 1 | medium | 71.0 | 287 | 1,345 | Rogers Branch |
| 2 | low flow | — | 331 | 146 | Stillhouse Branch |
| 3 | low flow | — | 414 | 123 | unnamed (NHDPlus 25000300018275) |
| 4 | low flow | — | 247 | 108 | Dry Branch |
| 5 | low flow | — | 409 | 108 | unnamed (NHDPlus 25000300000272) |
| 6 | low flow | — | 408 | 105 | Cordell Lane Branch |
| 7 | low flow | — | 351 | 92 | unnamed (NHDPlus 25000300026394) |
| 8 | low flow | — | 458 | 91 | Merriman Branch |
| 9 | low flow | — | 358 | 88 | unnamed (NHDPlus 25000300029341) |
| 10 | low flow | — | 312 | 83 | Rankin Branch |
| 11 | low flow | — | 343 | 83 | Robinson Branch |
| 12 | low flow | — | 364 | 83 | Austin Branch |
| 13 | low flow | — | 386 | 82 | Cookston Cave Creek |
| 14 | low flow | — | 221 | 82 | unnamed (NHDPlus 25000300035657) |
| 15 | low flow | — | 341 | 76 | unnamed (NHDPlus 25000300012442) |
| 16 | low flow | — | 343 | 73 | unnamed (NHDPlus 25000300022629) |
| 17 | low flow | — | 365 | 70 | Hester Branch |
| 18 | low flow | — | 382 | 68 | unnamed (NHDPlus 25000300027853) |
| 19 | low flow | — | 151 | 65 | unnamed (NHDPlus 25000300012438) |
| 20 | low flow | — | 149 | 63 | Easterly Branch |

Rogers Branch is the only corridor that reaches 1 kW. Ranks 2–6 are the plateau-edge streams that passed on the gauge transfer; on the regional yield they make 105–146 W, so they'd need about 7–10× the estimated flow (a spring, or the valley's limestone baseflow) to reach 1 kW. The 575 low-flow corridors are all small basins on the regional yield; their median is 9 W. With `--no-regional-yield` the run looks like the previous section's (6 ranked at 1 kW or more, 272 head only), plus 298 low-flow corridors.

## Five counties (PART 13, profile hydro_v3, 2026-10-20)

Marion, Bledsoe, Grundy and Rhea, the counties around Sequatchie on the same valley and plateau edge, were run with the same pipeline (`make ingest analyze score AOI=<key>`). Two rules changed first, both because Marion showed problems Sequatchie didn't have (DECISIONS.md change log, 2026-10-20):

- **No more mean-flow fallback where a regional yield exists.** Nine Marion reaches big enough for StreamStats got no usable answer (wrong basin, or across the Alabama line) and fell back to the NHDPlus mean annual flow. Two 1.5–2 sq mi basins ranked 4th and 5th at 28 and 23 kW. They now get their basin's regional yield ([ASSUMPTIONS.md](ASSUMPTIONS.md#regional-low-flow-yield)).
- **10 ft minimum net head for every tier** (profile `hydro_v3`). Marion's #3 "medium" site was the Sequatchie River itself, 1.2 kW on 1 ft of head from 392 sq mi, and seven low-confidence entries were big-river channels in the Sequatchie Valley above Jasper with 3–9 ft, on the Whitwell gauge transfer. A small turbine needs real drop; these need a dam.

Sequatchie was re-run on the new code (hydro run 71, score run 72) and comes out the same as hydro run 36 above.

| County | Hydro / score run | Perennial reaches | Small basins (< 1.3 sq mi) | StreamStats Q90 | ≥ 1 kW (all medium confidence) | Low flow | Filtered: < 1 kW on StreamStats | Filtered: < 10 ft |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Sequatchie | 71 / 72 | 989 | 635 | 350 | 1 | 575 | 349 | 63 |
| Marion | 69 / 70 | 2,382 | 1,763 | 590 | 2 (+1 head only) | 1,503 | 587 | 285 |
| Bledsoe | 59 / 60 | 2,019 | 1,492 | 503 | 0 | 1,217 | 503 | 297 |
| Grundy | 65 / 66 | 1,364 | 910 | 442 | 3 | 743 | 439 | 177 |
| Rhea | 67 / 68 | 2,046 | 1,583 | 429 | 0 | 1,188 | 429 | 425 |

Each county's run includes the reaches within 500 m of its line, so 485 reaches near a county line were analyzed twice (8,800 rows, 8,315 reaches). The map shows each reach once, from the county it sits in; the exports and the counts above are per county and include them.

**Every site at 1 kW or more, all five counties** (no low-confidence site reaches 1 kW anywhere):

| County | Rank | Score | Net head (ft) | Power (W) | Drainage (sq mi) | Stream |
| --- | --- | --- | --- | --- | --- | --- |
| Sequatchie | 1 | 71.0 | 287 | 1,345 | 1.61 | Rogers Branch |
| Marion | 1 | 65.5 | 255 | 1,031 | 1.75 | unnamed (NHDPlus 25000300021756) |
| Grundy | 1 | 63.5 | 127 | 1,570 | 6.43 | Negro Den Creek |
| Marion | 2 | 55.4 | 121 | 1,162 | 5.20 | Bethel Creek |
| Grundy | 2 | 51.5 | 94 | 1,184 | 6.80 | Elk River |
| Grundy | 3 | 40.0 | 41 | 1,005 | 43.58 | Big Creek |

Six sites across 8,315 reaches. Rogers Branch and Marion's unnamed stream are steep plateau-edge streams just past the regression's minimum; Negro Den Creek, Bethel Creek and the Elk River headwater are larger basins with moderate head; Big Creek is a 44 sq mi basin that clears 1 kW on 41 ft.

**Top 10 per county** (after the sites above, everything is low flow: under 1 kW on the regional yield, so listed by power):

| Rank | Marion | Bledsoe | Grundy | Rhea |
| --- | --- | --- | --- | --- |
| 1 | unnamed 25000300021756: 1,031 W, 255 ft | Maise Creek: 706 W, 397 ft | Negro Den Creek: 1,570 W, 127 ft | Henderson Creek: 678 W, 52 ft (32 sq mi) |
| 2 | Bethel Creek: 1,162 W, 121 ft | Clark Branch: 609 W, 385 ft | Elk River: 1,184 W, 94 ft | Richland Creek: 265 W, 13 ft (50 sq mi) |
| 3 | Mill Creek: head only, 121 ft | unnamed 25000300016759: 507 W, 369 ft | Big Creek: 1,005 W, 41 ft | unnamed 25000300032855: 261 W, 13 ft |
| 4 | Kimball Cove Branch: 577 W, 299 ft | Mill Branch: 477 W, 372 ft | unnamed 25000200041180: 552 W, 317 ft | unnamed 25000300007091: 239 W, 12 ft |
| 5 | unnamed 25000200067345: 522 W, 352 ft | Dry Branch: 390 W, 247 ft | unnamed 25000200062550: 462 W, 254 ft | unnamed 25000300002135: 235 W, 12 ft |
| 6 | Wildcat Branch: 455 W, 206 ft | unnamed 25000300000272: 388 W, 409 ft | unnamed 25000200016186: 419 W, 309 ft | unnamed 25000300019694: 214 W, 20 ft |
| 7 | unnamed 25000200025708: 445 W, 467 ft | Cold Spring Cove Branch: 365 W, 286 ft | unnamed 25000200060161: 397 W, 207 ft | Dunlap Creek: 156 W, 50 ft |
| 8 | unnamed 25000200060161: 419 W, 207 ft | unnamed 25000300012454: 342 W, 394 ft | unnamed 25000200044121: 386 W, 205 ft | Yarborough Branch: 148 W, 295 ft |
| 9 | Glover Branch: 409 W, 490 ft | Rains Creek: 338 W, 185 ft | Bostick Creek: 340 W, 193 ft | unnamed 25000300036459: 140 W, 13 ft |
| 10 | unnamed 25000200067901: 402 W, 232 ft | Rocky Branch: 338 W, 221 ft | unnamed 25000200021388: 329 W, 177 ft | Gilbreath Creek: 135 W, 255 ft |

(Sequatchie's top 10 are in the previous section; they didn't change.) Mill Creek, on Marion's list, is head only because its outlet is just over the line in Grundy, outside the HUC8 basins fetched for Marion; the map shows it with Grundy's numbers. Bledsoe's Dry Branch and unnamed 25000300000272 are Sequatchie's #4 and #5, and Marion's and Grundy's unnamed 25000200060161 is the same reach: border reaches with different numbers in each county (below).

**What stands out:**

- **The low-flow lists are much stronger outside Sequatchie** (best 550–710 W against 146 W), because the regional yield differs by county. Each county's run builds it from its own StreamStats reaches only:

  | HUC8 | Sequatchie | Marion | Bledsoe | Grundy | Rhea |
  | --- | --- | --- | --- | --- | --- |
  | `06020004` Sequatchie River | 0.0188 | 0.0215 | **0.0675** | 0.0196 | — |
  | `06020001` Walden Ridge, Tennessee River side | 0.0172 | 0.0157 | 0.0161 | — | 0.0192 |
  | `05130107` Collins River | 0.0186 | — | — | 0.0209 | — |
  | `05130108` Caney Fork | 0.0184 (pooled) | — | 0.0166 | — | — |
  | `06030001` Guntersville Lake (Battle Creek) | — | **0.0834** | — | 0.0216 | — |
  | `06030003` Upper Elk River | — | — | — | **0.0791** | — |
  | `06010201` Watts Bar Lake | — | — | 0.0296 | — | 0.0218 |

  (cfs per sq mi.) Bledsoe's part of the Sequatchie basin is the upper valley, and Marion's Battle Creek side and Grundy's Elk River side are limestone; StreamStats rates their bigger streams 3–4× wetter per square mile than the plateau. That's plausible, but it means a reach near a county line can get a 3–4× different flow depending on which county's run it's in (Sequatchie's unnamed 25000300000272: 108 W in Sequatchie's run, 388 W in Bledsoe's). Pooling the yield across counties would fix it; not done yet.
- **Rhea has no steep high-yield streams.** Its top entries are big basins (27–50 sq mi) near the Tennessee River with 12–52 ft of head, where StreamStats outlined the wrong watershed and the regional yield now fills in (before PART 13 they'd have been on the mean flow, at several kW). Its best plateau-edge stream, Yarborough Branch, makes 148 W.
- **Bledsoe has nothing at 1 kW** on StreamStats or the regional yield, despite 503 StreamStats reaches. An earlier run made it look like 8 sites at 1.3–2.6 kW: StreamStats went down mid-run and 159 reaches fell back to the Whitwell gauge transfer (about 10× high). A bug kept the run from saying so; it's fixed ([ASSUMPTIONS.md](ASSUMPTIONS.md#streamstats), "Outages").

## Not built yet

- **The map colors stretches by hydro score**, not overall score; the popup shows both.
- **FR-3 fallback profile.** §3 of the plan keeps the original weight table (head 40, watershed area 30, length 15, rainfall 15) as a labeled screening profile. It needs its own breakpoints for watershed area and length, and rainfall data (D-10).
- **Sensitivity check.** DECISIONS.md asks how much the top 20 changes under 70/30 vs 50/50 vs 100/0 weights. Each alternative is just a new profile file and a re-score.
