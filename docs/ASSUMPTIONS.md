# Hydro Analysis Assumptions

How `hhsf analyze hydro` turns each stream reach into head, firm flow and firm power, and every assumption behind those numbers. The judgment calls themselves live in [DECISIONS.md](DECISIONS.md); the values used are in [`config/hydro.yaml`](../config/hydro.yaml). Every run copies them into `analysis_runs.params`, so you can always see which assumptions produced a given result.

**Ground rule:** when a number can't be estimated reliably it is stored as **empty (NULL) with a flag** explaining why. The tool never fills a gap with a made-up value.

## What one row in `hydro_corridors` is

A **corridor is the steepest stretch of one perennial stream reach** (one NHDPlus HR flowline) that a single penstock could cover ([D-08](DECISIONS.md#d-08--what-counts-as-one-corridor-measure)): at most **1,000 ft** (`corridor.penstock_length_ft` in `config/hydro.yaml`). There is still one row per reach per run, but its geometry, length, head and power are for that stretch only:

| Column | Meaning |
| --- | --- |
| `geom` | Just the stretch, drawn upstream to downstream. Its first point is where an intake would go, its last point where the turbine would go. |
| `length_m` | The stretch's length along the stream (at most 304.8 m). A reach shorter than that is one stretch, end to end. |
| `from_measure`, `to_measure` | Where the stretch sits on its reach, in percent along the reach with **0 at the downstream end** (the NHDPlus convention). `from_measure` is the stretch's bottom, `to_measure` its top. A whole reach is 0 to 100. |
| `head_method` | `dem_3dep_steepest_stretch` for a stretch; `nhdplus_vaa_smoothed_reach` for the older whole-reach method (runs made with `--no-terrain`, and every run before this change). |

Of Sequatchie's 989 perennial reaches, 878 are longer than 1,000 ft, so their corridor is part of the reach; the other 111 are whole reaches.

**Flow stays the reach's.** D-08 only changes where the head comes from. The drainage area, and so the flow, is still measured at the reach's downstream end (its pour point). A stretch near the top of a long reach drains a little less than that, so its flow and power are slightly high. Reaches are short enough (median 880 m) that this is small next to the flow estimate's own error.

### How the steepest stretch is found

Code: `src/hhsf/analysis/stretch.py`. Elevations come from the **USGS 3DEP 1/3 arc-second DEM** (about 10 m cells, meters above NAVD88), loaded by `hhsf ingest terrain` (part of `make ingest`).

1. **Sample** the DEM every **10 m** along the reach (`corridor.sample_spacing_m`), upstream to downstream, measured in EPSG:5070. Each sample takes the value of the DEM cell it falls in.
2. **Clean the profile** so it never rises going downstream: each sample is lowered to the lowest elevation above it (a "carve"). That removes bumps where a road fill or culvert crosses the stream, or where the NHDPlus line strays onto a bank. Then the profile is kept between NHDPlus's smoothed elevations for the reach's two ends (`max_elev_m`, `min_elev_m`), so **a stretch never drops more than NHDPlus says the whole reach does**.
3. **Slide a 1,000 ft window** down the cleaned profile, starting at every sample, and keep the one with the most drop. The window's bottom elevation is interpolated between samples, so the stretch is exactly 1,000 ft. If several windows tie (a flat reach, or a uniform slope), the most downstream one wins: it has the most water.
4. **Gross head** is that drop; net head, firm power and the score follow as below.

If part of a reach is off the DEM or on a cell with no data, its head is NULL with `head_flag = no_terrain_data` (none in Sequatchie: the DEM covers every loaded stream plus about 500 m). If a flowline has pieces that don't join, `head_flag = reach_not_one_line` (none in Sequatchie either).

**What it changed in Sequatchie** (2026-10-19, hydro run 22 vs. the whole-reach run 12):

| | Whole reach | Steepest 1,000 ft |
| --- | --- | --- |
| Median gross head | 27 m (88 ft) | 13 m (42 ft) |
| 90th percentile | 122 m (401 ft) | 64 m (209 ft) |
| Largest net head ranked with a flow estimate | 1,101 ft | 414 ft |
| Reaches at or above 1 kW | 66 (14 medium confidence) | 19 (1 medium confidence) |

The biggest stretch drops are real: the streams coming off the Cumberland Plateau escarpment into the Sequatchie Valley fall 150–250 m in under 500 m, and NHDPlus agrees. 31 of the 878 long reaches hit the NHDPlus cap in step 2 (the DEM saw at least the whole reach's drop within one window).

**Limits:**

- **Penstock length is measured along the stream.** A real penstock can cut across bends, so on a winding stream it could reach a little more drop for the same pipe length. The search doesn't try to route a pipe.
- **10 m cells blur narrow features.** A small waterfall or a ravine narrower than a cell or two can be under- or over-stated by a few meters. Confirm the drop on the visit (or with 1 m LiDAR, later).
- **The NHDPlus line and the DEM channel don't always line up.** Where the line runs along a bank, samples read high; the carve and the NHDPlus cap remove most of that, but not all.

**Changing the length:** set `corridor.penstock_length_ft` in `config/hydro.yaml`, or for one run, `uv run hhsf analyze hydro --penstock-ft 2000`. The D-08 sensitivity check on 2026-10-19 gave (before the [regional yield](#regional-low-flow-yield) replaced the gauge transfer on small basins; with it, no corridor is head-only and only the medium-confidence column still holds, so 1 kW at 1,000 ft is Rogers Branch alone):

| Longest penstock | Reaches at or above 1 kW | of them medium confidence | Head-only corridors ranked |
| --- | --- | --- | --- |
| 500 ft | 14 | 0 | 258 |
| **1,000 ft (default)** | **19** | **1** | **272** |
| 2,000 ft | 33 | 3 | 275 |
| Whole reach (old method) | 66 | 14 | 283 |

## Metrics

### Gross head (`gross_head_m`)

**Default (`head_method = dem_3dep_steepest_stretch`):** the drop over the corridor's stretch, from the cleaned 3DEP profile described [above](#how-the-steepest-stretch-is-found):

```
gross_head_m = elevation at the stretch's top − elevation at its bottom     (3DEP DEM, m)
```

A flat stretch has head **0**, a real value.

**Whole reach (`head_method = nhdplus_vaa_smoothed_reach`, `--no-terrain` runs and older runs)** ([site-selection.md B9](../site-selection.md)): the elevation drop between the reach's upstream and downstream ends, from NHDPlus HR **smoothed** elevations:

```
gross_head_m = max_elev_m − min_elev_m        (NHDPlus maxelevsmo − minelevsmo, cm → m)
```

- Smoothing makes elevations never rise going downstream, so gross head is never negative. A flat reach has head **0** (29 reaches in Sequatchie), which is a real value, not missing data.
- If either elevation is missing: NULL, `head_flag = missing_elevation`. If the data says the stream rises downstream: NULL, `head_flag = elevation_rises_downstream`. (Neither happens in Sequatchie today.)
- Smoothed elevations come from the 10 m-era NHDPlus DEM processing, not survey. Expect errors of a few meters; a site visit (or later, 1 m LiDAR) confirms the real drop.

### Net head (`net_head_m`)

```
net_head_m = gross_head_m × 0.85              (D-07)
```

A planning allowance for pipe friction in a reasonably sized penstock. Real losses depend on pipe diameter and length and are worked out per site later.

### Corridor length (`length_m`)

The stretch's length along the stream (at most the penstock length), or for a whole-reach corridor, `streams.length_m`. Both are measured in EPSG:5070 (equal-area, meters) per the CRS contract in `src/hhsf/core/crs.py`. The whole reach's length is still in `streams.length_m`.

### Drainage area (`drainage_area_sqkm`)

NHDPlus HR `totdasqkm`: total upstream area at the reach's downstream end. `drainage_area_method = nhdplus_vaa_totdasqkm`.

### Firm flow (`firm_flow_cfs`)

Target statistic: **Q90**, the flow met or exceeded 90% of the time ([D-04](DECISIONS.md#d-04--definition-of-reliable-flow-default)). The choice, in order:

| Case | `firm_flow_cfs` | `flow_method` | `flow_confidence` | `flow_flags` |
| --- | --- | --- | --- | --- |
| StreamStats Q90, all basin characteristics inside the equation's limits | StreamStats D90 | `streamstats_q90` | `medium` | — |
| StreamStats Q90, some characteristic other than drainage area outside the limits | StreamStats D90 | `streamstats_q90` | `low` | `out_of_range:<CODE>` |
| The outlet's basin was the wrong one, but a point further up the reach found the right one | StreamStats D90 there × (outlet area ÷ area there) | `streamstats_q90` | `medium` (or `low` if out of range) | `streamstats_pour_point_moved` |
| Basin smaller than the regression's minimum drainage area (1.3 sq mi), anywhere | Drainage area × the median StreamStats Q90 per sq mi of the in-range reaches in its HUC8 | `regional_streamstats_yield` | `low` | `below_regression_min_drainage_area`, `regional_yield_basin:<code>` |
| No StreamStats Q90 for a larger basin (service down, failed for this point, wrong basin, or switched off), and the reach drains to the AOI's reference gauge's basin. Also small basins there when no regional yield is found or the run used `--no-regional-yield` | Gauge Q90 × (reach area ÷ gauge area) | `gauge_drainage_area_ratio` | `low` | the reason (e.g. `streamstats_drainage_area_mismatch`), plus `area_ratio_outside_0.5_1.5` when it applies |
| Small basin outside the gauge's basin with no regional yield (no basin with enough StreamStats Q90s, or `--no-regional-yield`) | **NULL** | — | `below_applicability` | `below_regression_min_drainage_area` |
| No StreamStats Q90 for a larger basin (service down, failed for this point, wrong basin, or the reach is across the state line), not in the gauge's basin | Drainage area × the [regional yield](#regional-low-flow-yield) of its HUC8 | `regional_streamstats_yield` | `low` | the reason, plus `regional_yield_basin:<code>` |
| The same, but no regional yield either (StreamStats down from the start, switched off, or `--no-regional-yield`) | NHDPlus EROM **mean annual** flow | `nhdplus_erom` | `low` | the reason, plus `mean_flow_not_q90` |
| No drainage area or no mean flow | NULL | — | `unavailable` | the reason |

Notes:

- **Why "medium" and never "high":** the Tennessee flow-duration equations (Law and others, 2009, USGS SIR 2009-5159) have an average standard error of prediction of about **60% for D90**. The value is stored in `flow_std_error_pct`.
- **Minimum drainage area:** 1.3 sq mi (3.37 km²) for the Central and East Tennessee region (`GC1419`, which covers Sequatchie); 2.0 sq mi for West Tennessee (`GC1418`). StreamStats will happily extrapolate below the limit and only add a warning, so the tool checks the limit itself and never asks StreamStats about these basins. Below it, the flow comes from the regional yield (the gauge transfer only if that is missing or switched off), low confidence and keeping the `below_regression_min_drainage_area` flag ([D-09](DECISIONS.md#d-09--small-streams-below-streamstats-applicability-limit-measure)). If neither applies, there is no flow estimate: the corridor keeps its head and is marked `below_applicability` instead of being dropped.
- **In Sequatchie, 635 of 989 reaches (64%) are below the minimum.** Only 354 are big enough for the regression. That's the share D-09 said to measure. All 634 with a drainage area now get the [regional yield](#regional-low-flow-yield), so no reach is head-only any more.
- **The last fallback overstates flow.** EROM mean annual flow is always higher than Q90, often by a large factor on small streams (about 130× in Sequatchie). It's used only so a corridor isn't left blank when nothing better exists; the `low` confidence and `mean_flow_not_q90` flag mark it. Since PART 13 the regional yield takes its place wherever the run has StreamStats answers to build one, so it mostly shows up when StreamStats was down for the whole run. Re-run once StreamStats is back to replace these values.
- **Wrong-basin check:** StreamStats snaps the pour point to its own stream grid. If the basin it delineates differs from the NHDPlus drainage area by more than 25%, it probably snapped to a different stream. The tool then tries points further up the reach; see [When StreamStats snaps to the wrong stream](#when-streamstats-snaps-to-the-wrong-stream).
- **Pour point:** 30 m above the reach's downstream end (at most 10% of the reach), so StreamStats doesn't snap onto the confluence with the next reach. NHDPlus flowlines are drawn upstream to downstream; this was checked on the loaded data (every end-to-start connection goes to an equal or larger drainage area).
- `mean_flow_cfs` (EROM) is copied onto every row for reference, whatever method was used.

### When StreamStats snaps to the wrong stream

StreamStats doesn't use the NHDPlus flowline. It moves the pour point onto the nearest cell of its own 10 m stream grid, and the two don't always line up. Near a confluence the point can land on the bigger stream (Conner Creek's outlet came back as a 31.9 km² basin instead of 8.6 km²); elsewhere it lands on a hillside cell that drains almost nothing (Cain Creek: 0.6 km² instead of 55.7 km²). So every answer is checked, and a wrong one gets a second try:

1. **Check at the outlet.** If StreamStats' drainage area (`DRNAREA`) differs from NHDPlus's by more than 25% (`max_drainage_area_mismatch`), it's the wrong basin.
2. **Try further up the reach.** The tool asks again at 25%, 50% and 75% of the reach's length up from its downstream end (`pour_point_retry_shares` in `config/hydro.yaml`) and keeps the first point whose basin passes the same 25% check against the NHDPlus drainage area *at that point*. NHDPlus only gives the area at a reach's outlet, so the area partway up is a straight line between the outlet and the reach's top, where it's the sum of the streams flowing in (zero for a headwater).
3. **Scale back to the outlet.** The Q90 found there is multiplied by outlet area ÷ area at the point, the same drainage-area ratio the gauge transfer uses. Points that drain less than half the outlet's area are skipped (`min_pour_point_area_share`), so the step is small, and the regression's own exponent on drainage area is 1.045, so straight scaling is close to what it would give. Confidence stays `medium`: the ~60% regression error dwarfs this step. `flow_flags` gets `streamstats_pour_point_moved`, and `streamstats_drainage_area_sqkm` holds the area StreamStats found at the moved point.
4. **If no point matches,** the estimate is discarded (`streamstats_drainage_area_mismatch`) and the gauge, regional-yield or EROM fallback applies.

**In Sequatchie** (hydro run 24, 2026-10-19), 22 reaches failed the check at the outlet. The retry found the right basin for 18 of them, which were all the reaches that had been on the EROM mean-flow fallback. Their Q90s work out to 0.015–0.019 cfs per sq mi, in line with the other StreamStats reaches (median 0.018). The EROM mean flow they had before was 130–190× that, which had put 13 of them on the 1 kW shortlist at 1–21 kW. On Q90 the best of them gives 159 W. The other 4 stay on the gauge transfer: three unnamed 176 km² reaches where StreamStats returns no usable basin at any point, and Minton Branch, about 30% off at every point. No reach is on the EROM fallback any more, unless StreamStats is down.

### Gauge transfer (drainage-area ratio)

When StreamStats can't give a Q90, a reach that drains to the AOI's reference gauge's basin borrows the gauge's measured Q90, scaled by drainage area:

```
Q90_reach = Q90_gauge × (drainage_area_reach ÷ drainage_area_gauge)
```

- **Gauge:** set per AOI in `config/aoi/<key>.yaml` (`reference_gauge`). For Sequatchie: **USGS 03571000, Sequatchie River near Whitwell, TN**, 402 sq mi, in the Sequatchie HUC8 basin (`06020004`).
- **Gauge Q90:** the 10th percentile of approved daily mean flows (NWIS, parameter 00060), water years 2002–2025 (`config/hydro.yaml`, `gauge_transfer`). For Whitwell: **77.2 cfs from 8,400 days**, or 0.19 cfs per sq mi. (Mean flow over the same period is 803 cfs, about 10× Q90, which shows how much the EROM mean-flow fallback overstates firm flow.) A gauge with less than 10 years of approved record is refused.
- **Which reaches:** only those whose downstream end is inside the gauge's HUC8 (from the Watershed Boundary Dataset): 522 of Sequatchie's 989. Since D-09 was decided (2026-10-19), only the larger ones without a StreamStats Q90 use it: 4 in hydro run 36 (three unnamed 176 km² reaches StreamStats can't outline, and Minton Branch). Small basins there get the [regional yield](#regional-low-flow-yield) instead, because the gauge runs about 10× high on them. The other 467 reaches are outside the gauge's basin, in two places (checked 2026-10-19 from NHDPlus region codes and the HUC8 StreamStats reports for each pour point):
  - **285 drain east off Walden Ridge to the Tennessee River** (Middle Tennessee-Chickamauga, HUC8 `06020001`): Soddy, North Suck, Cain, Conner and Brock Creeks and their tributaries.
  - **182 drain west to the Cumberland**, mostly the Collins River (HUC8 `05130107`), a few the Caney Fork (`05130108`).
  - A second gauge for these two areas was tried and rejected: Collins River near McMinnville gives the same ~10× as Whitwell, and the Walden Ridge gauges disagree with each other. See [Regional low-flow yield](#regional-low-flow-yield).
- **Confidence is always `low`.** The method assumes every square mile of the basin yields the same low flow. The usual guidance is to trust it for basins between half and one and a half times the gauge's size; every reach in the county is far smaller (largest ratio ≈ 0.17), so every transferred value also carries `area_ratio_outside_0.5_1.5`. `flow_reference_gauge` and `flow_drainage_area_ratio` record the gauge and ratio used.
- **Basins below the regression minimum** only get a transferred value when the regional yield can't be used (`--no-regional-yield`, or no basin with enough StreamStats reaches), with `below_regression_min_drainage_area` kept in `flow_flags`.
- **StreamStats wins** wherever it gives an in-range Q90; the gauge only fills gaps.
- **How far off it can be:** see [Gauge transfer vs. StreamStats](#gauge-transfer-vs-streamstats) below.

### Gauge transfer vs. StreamStats

Checked on 2026-10-19 against the first 145 reaches StreamStats answered (87 of them in the Whitwell gauge's basin), comparing each one's StreamStats Q90 with what the gauge transfer would have given:

| | Median | 10th–90th percentile |
| --- | --- | --- |
| Gauge transfer ÷ StreamStats Q90 (reaches in the gauge's basin) | **10.5×** | 1.5× – 11.7× |
| StreamStats Q90 per sq mi | 0.018 cfs | 0.016 – 0.13 cfs |
| Whitwell gauge Q90 per sq mi | 0.19 cfs | — |
| NHDPlus EROM mean flow ÷ StreamStats Q90 | 134× | — |

So on most of these streams **the gauge transfer gives about ten times the low flow StreamStats does.** The likely reason is geology: the Whitwell basin includes the limestone Sequatchie Valley floor, where springs keep the river flowing in dry weather, while most of the county's small perennial streams drain the sandstone plateau and its slopes, which the regression's recession and soil terms treat as giving far less baseflow. Neither number is measured at the site, and both carry large errors, but the gap is systematic, not noise.

**How to use it:** treat a gauge-transfer value as an *upper bound*. A site that misses 1 kW on it almost certainly misses on the real flow; a site that passes needs a dry-season flow check on the visit. (The EROM mean-flow fallback is far worse, about 130× StreamStats' Q90, which is why the gauge replaces it wherever it can.)

### Regional low-flow yield

Every small basin (below the regression's 1.3 sq mi minimum) borrows the low flow per square mile of the bigger streams around them ([D-09](DECISIONS.md#d-09--small-streams-below-streamstats-applicability-limit-measure), decided 2026-10-19):

```
Q90_reach = drainage_area_reach (sq mi) × median( StreamStats Q90 ÷ drainage area )
                                          over the medium-confidence StreamStats reaches in the same HUC8
```

- **Which streams lend their yield:** reaches in the same run with a `medium` StreamStats Q90 (every basin characteristic in range, including moved pour points), grouped by the WBD 8-digit basin (HUC8) their downstream end is in. Out-of-range, gauge and fallback flows are never used.
- **Too few lenders:** a HUC8 needs at least **10** of them (`regional_yield.min_reaches` in `config/hydro.yaml`). If it has fewer, the HUC8s sharing its first 6 digits (then 4) are pooled. `flow_flags` gets `regional_yield_basin:<code>`, and the code's length shows which level was used.
- **Confidence is always `low`**, and `below_regression_min_drainage_area` stays in `flow_flags`, so D-09 sites can still be picked out. `flow_method = regional_streamstats_yield`. The yield used per basin, and how many reaches it came from, are in the run's `summary` (`regional_yield`).
- **It replaces the mean-flow fallback on larger basins** (PART 13). A reach big enough for StreamStats that got no usable answer, outside the gauge's basin, gets the regional yield instead of the NHDPlus mean annual flow, which runs about 100× a Q90. In Marion County, before this, nine such reaches were on the mean flow and two 1.5–2 sq mi basins ranked 4th and 5th at 28 and 23 kW; three of the nine are just over the Alabama line, where Tennessee's StreamStats can't delineate. These keep the reason in `flow_flags` and don't get `below_regression_min_drainage_area`.
- **It replaces the gauge transfer on small basins.** Inside the reference gauge's HUC8 a small basin gets the regional yield too, because the gauge transfer runs about 10× StreamStats there (below). If no regional yield can be found, it keeps the gauge transfer. The gauge still covers larger reaches StreamStats couldn't answer for.
- **It's per county.** Each AOI's run builds its yields from its own StreamStats reaches (including the ones within its buffer). Across the five counties the same HUC8 got yields up to 3.9× apart (`06030001`: 0.083 from Marion's reaches, 0.022 from Grundy's), so a reach near a county line gets a different flow in each county's run. See [SCORING.md](SCORING.md#five-counties-part-13-profile-hydro_v3-2026-10-20).
- **To switch it off** for one run: `uv run hhsf analyze hydro --no-regional-yield`. Small basins then go back to the gauge transfer (in its basin) or no flow, ranked head only (elsewhere), the D-09 starting rule.

**In Sequatchie** (hydro run 36, 2026-10-20), all 634 small basins with a drainage area got a value:

| Basin | Yield (cfs per sq mi) | StreamStats reaches it came from | Small basins filled |
| --- | --- | --- | --- |
| `06020004` Sequatchie River (replaces the Whitwell gauge transfer) | 0.0188 | 179 | 338 |
| `06020001` Walden Ridge, Tennessee River side | 0.0172 | 112 | 173 |
| `05130107` Collins River | 0.0186 | 57 | 103 |
| `051301` (Caney Fork `05130108` has only 2 StreamStats reaches, so pooled with Collins) | 0.0184 | 59 | 20 |

Their basins are 0.02–1.29 sq mi, so their Q90 is about 0.0003–0.024 cfs (0.1–11 gpm). **None reaches 1 kW:** the best is 146 W (Stillhouse Branch, 331 ft of net head, which made 1.5 kW on the gauge transfer), and only 5 pass 100 W, all plateau-edge streams in the Sequatchie basin that had been on the shortlist on the gauge transfer. Outside that basin the best is 56 W (Panther Branch). Even Stillhouse Branch would need about 7× this yield to reach 1 kW. So the head-only tier is empty, and these corridors are scored as `low_flow` (listed last, see [SCORING.md](SCORING.md)) instead of filtered out.

#### How the two candidate methods compared

Checked on 2026-10-19 with hydro run 28 and NWIS daily records. Q90 is the 10th percentile of approved daily means; "yield" is Q90 per square mile.

**A. More reference gauges** (one per HUC8):

| Gauge | Basin | Area (sq mi) | Record | Q90 yield | ÷ StreamStats yield in that HUC8 (median) |
| --- | --- | --- | --- | --- | --- |
| 03571000 Sequatchie River near Whitwell | `06020004` | 402 | WY 2002–2025 | 0.192 | 10.2× |
| 03421000 Collins River near McMinnville | `05130107` | 640 | WY 2002–2025 | 0.186 | **10.0×** |
| 03566525 North Chickamauga Creek near Montlake | `06020001` | 60.6 | 2000–2010 (9.9 yr) | 0.023 | 1.3× |
| 03566535 North Chickamauga Creek at Mile Straight | `06020001` | 74 | 2016–2025 (9 yr) | **0.000** | 0× |
| 03544500 Richland Creek near Dayton | `06020001` | 50.2 | 1927–1981 | 0.006 | 0.35× |
| 03569190 Mountain Creek near Glendale | `06020001` | 5.07 | 2019–2025 (6 yr) | 0.087 | 5.1× |

Collins River near McMinnville repeats Whitwell's problem: its basin reaches the limestone Highland Rim (Barren Fork near Trousdale, a Collins tributary, runs 0.39), so it is about ten times what StreamStats gives the county's plateau streams. The Walden Ridge side has no long current record: the four gauges that drain the ridge disagree with each other from zero to 0.087, North Chickamauga Creek's recent Q90 is zero (it goes dry), and none has 10 years of approved record in one recent stretch. Which gauge was picked would decide the answer, so this option was dropped.

**B. Regional yield** (median StreamStats yield of the same HUC8, the method used):

| Check | Result |
| --- | --- |
| StreamStats yield, 10th–90th percentile, `06020001` / `05130107` | 0.016–0.019 / 0.017–0.039 cfs per sq mi |
| Leave-one-out median ÷ each reach's StreamStats Q90, `06020001` | median 1.00, 10th–90th 0.92–1.09, all within 2× |
| The same, `05130107` | median 0.99, 10th–90th 0.48–1.10, 88% within 2× |
| The same, smallest in-range basins only (1.3–3 sq mi) | median 1.03 (`06020001`), 1.06 (`05130107`) |
| StreamStats' own answer below the limit, 27 of the highest-head small basins (it was asked to extrapolate; 3 more snapped to the wrong basin) | regional ÷ extrapolated: median **1.22** in `06020001` (12 basins, 1.0–1.35), 1.14 in `05130108` (3), but **0.27** in `05130107` (12, 0.20–1.09) |
| Measured plateau gauges (no limestone valley in the basin): North Chickamauga, Richland Creek, Caney Fork at Clifty (1931–1949), Bee Creek at Herbert (1930–1937) | 0.000–0.023 (Clifty 0.011, Herbert 0.008), around the regional 0.017–0.019. The exception is Mountain Creek near Glendale, 0.087 on a 6-year record. |

So the regional yield matches StreamStats wherever StreamStats applies, and it agrees with the gauges that drain only plateau sandstone. It doesn't have the 10× high bias of the big-river gauges.

**Limits:**

- **It is an extrapolation.** The regression wasn't fitted on basins this small. Its drainage-area exponent (1.045) is close to 1, so yield barely depends on size, but the other basin characteristics of a tiny basin can differ from its neighbors'.
- **The Collins River side may be up to 5× low.** For 10 of the 12 Collins basins StreamStats was asked about, its own extrapolation gave 1.6–5× the regional yield (0.03–0.09 cfs per sq mi), driven by a higher streamflow-recession index (`RECESS` up to 50; most streams in the county sit at the equation's lower limit of 32). Even at 5× none of them would reach 300 W.
- **Springs are invisible to it.** A spring-fed stream can carry many times its basin's share in dry weather, as the limestone valley does. A visit, or a dry-season flow measurement, is the only way to find one.
- **The Sequatchie River basin mixes two kinds of stream.** About a fifth of its StreamStats reaches, on the limestone valley floor, yield 5–10× the median (90th percentile 0.15 cfs per sq mi). A small spring-fed stream on the valley floor could likewise carry far more than this estimate; the plateau-edge streams with the most head are more like the median.
- **It is about a tenth of the gauge transfer for the same size of basin** (0.019 against 0.19 cfs per sq mi). That gap is why D-09 chose it for small basins: the gauge's number comes from a 402 sq mi river fed by valley springs. If you'd rather see the gauge's upper bound for the Sequatchie-side streams, run with `--no-regional-yield`.

### Usable flow and firm power (`usable_flow_cfs`, `firm_power_w`)

```
usable_flow  = firm_flow × 0.5                       (D-05: half can be diverted; the rest stays in the stream)
firm_power_W = ρ · g · Q · H · η
             = 1000 kg/m³ × 9.81 m/s² × usable_flow (m³/s) × net_head (m) × 0.5   (D-06)
```

- 1 cfs = 0.0283168 m³/s.
- η = 0.5 is the whole system (turbine, generator, controller, transmission). With gross head it reproduces the D-01 rule of thumb, *watts ≈ head (ft) × flow (gpm) ÷ 10*; on top of that this tool also takes the 85% net-head and 50% diversion allowances, so its numbers are deliberately conservative.
- Put together, firm power ≈ **0.21 × ρ·g·Q90·H_gross**.
- `power_confidence` follows the flow: `medium` for StreamStats Q90, `low` for anything else (gauge transfer, regional yield, out-of-range StreamStats, mean flow), `unavailable` (NULL power) when flow or head is NULL.
- The 1 kW target (D-01) is only reported in the run summary here. Filtering and scoring against it happen in `hhsf score hydro`; see [SCORING.md](SCORING.md).

## StreamStats

Endpoints, limits and timeouts are in [`config/data_sources.yaml`](../config/data_sources.yaml) under `streamstats`. Each pour point takes four calls (USGS's published workflow):

1. `ss-delineate` — delineate the watershed (GET, ~3–4 s)
2. NSS `regressionregions/bylocation` — which regression region it's in (POST, ~3 s)
3. `ss-hydro` `basin-characteristics/calculate` — drainage area, recession index, climate factor, soil permeability (POST)
4. NSS `scenarios/estimate` — evaluate the D90 equation (POST)

- **Rate limit:** USGS asks for **no more than four simultaneous requests** and says enforcement may change. No per-second limit is published and responses carry no rate-limit headers. The tool runs at most 4 reaches at a time.
- **Caching:** every successful response is saved under `data/cache/streamstats/` (by step and pour point), so a re-run only asks for what it doesn't have. Failures aren't cached. Delete the folder to force fresh answers.
- **Outages:** a 5xx, 429 or timeout is retried twice (2 s, then 4 s). After 3 reaches in a row find the service down, the run stops calling it and the remaining reaches fall back to the gauge transfer (in its basin), the regional yield (built from the answers it got before the outage) or EROM, flagged `streamstats_unavailable`. It stays off for the rest of the run, even if an answer already on its way comes back. The CLI prints "StreamStats looks unavailable" with how many reaches it affected; run it again later, since cached answers are kept.
- **Status:** on 2026-10-18 steps 1, 2 and 4 worked but step 3 (`ss-hydro`) returned **502 Bad Gateway** all day. The same step stopped answering again on 2026-10-20 around 11:00, partway through Bledsoe County. It was back on 2026-10-19, and its responses parse correctly (a list of `{code, value}` items). It's slow under load: delineations took up to ~15 s, and a full Sequatchie run took about 30 minutes (2026-10-19).
- The older `streamstatsservices/` endpoints are gone (404).

## Terrain (3DEP)

- **Source:** USGS 3D Elevation Program, 1/3 arc-second seamless DEM, "current" tiles on the National Map's S3 bucket (`config/data_sources.yaml`, `usgs_3dep_13`). Public domain.
- **What's downloaded:** USGS publishes 1×1 degree tiles; all of Sequatchie's streams sit in one, `n36w086` (472 MB, last updated 2026-04-29). The tiles are cloud-optimized GeoTIFFs, so `hhsf ingest terrain` reads only the part under the AOI's streams (plus about 500 m) over HTTP: **about 84 MB transferred, saved as a 48 MB file**, `data/raw/usgs_3dep_13/sequatchie_tn.tif`, in about 20 seconds. Re-running reuses the file unless the streams reach past it. The cells are copied as they are (same NAD83 grid, no resampling).
- **Provenance:** the tile names and last-modified dates go into `data_sources.version` and each run's `data_source_versions`.
- USGS rebuilds the 1/3 arc-second DEM from the best elevation source it has for each area (LiDAR where it exists), so it can be newer than the elevations NHDPlus HR was built on. Expect the two to differ by a few meters in places; the NHDPlus cap above keeps a stretch from claiming more drop than NHDPlus gives the whole reach.

## Runs

- Every `hhsf analyze hydro` writes a new `analysis_runs` row (`analysis = 'hydro'`) with the AOI, `git describe` code version, the data source versions, every assumption above (`params`, including `corridor_method` and the penstock length), and a summary of counts. Its corridors carry that `run_id`; earlier runs are never changed.
- `status` is `running` while it works, `finished` when saved, `failed` if it crashed (with no corridors).
- To read the latest run: `SELECT * FROM hydro_corridors WHERE run_id = (SELECT max(run_id) FROM analysis_runs WHERE analysis = 'hydro' AND status = 'finished')`.

## Parcels and owners

PART 14 links every corridor to the property it sits on, so the shortlist becomes a list of properties to visit and owners to contact.

- **Source:** the Tennessee Comptroller of the Treasury's statewide parcel layer, "Tennessee Property Boundaries Public Use" (`config/data_sources.yaml`, `tn_parcels`). It covers 86 of Tennessee's 95 counties, all five analyzed ones included, and is updated monthly. Chester, Davidson, **Hamilton** (Chattanooga), Hickman, Knox, Montgomery, Rutherford, Shelby and Williamson keep their own parcels and aren't in it.
- **Terms:** the layer is a public service, "not intended for commercial use", with no warranty. It's for graphical reference: the lines aren't legal descriptions or surveyed boundaries, and the owner names aren't proof of ownership. Check the deed (the TPAD link) before relying on either. The Comptroller's per-county shapefiles are the commercial route and add the full assessment table (mailing address, values), but they're downloaded by hand through a web form and the Comptroller asks for no automated downloads, so they aren't used.
- **What's loaded:** `hhsf ingest parcels --aoi <key>` (part of `make ingest`) queries one county at a time, 2,000 parcels per page, cached in `data/raw/tn_parcels/<aoi>/` (about 10 seconds per county). `--refresh` downloads again for the monthly update. A county's parcels are replaced as a whole on each ingest, because parcels split and merge. A handful of parcels are drawn as several pieces; they're merged into one.

| County | Parcels |
| --- | --- |
| Sequatchie | 12,652 |
| Marion | 22,263 |
| Bledsoe | 11,376 |
| Grundy | 11,947 |
| Rhea | 21,468 |

- **Parcel ID** (`parcels.parcel_id`) is the assessor's control map, group (often blank) and parcel number, e.g. `030 001.33` or `048M D 001.10`: what the county assessor and the Comptroller's site use. `gislink` is the Comptroller's key, used in their links.
- **Acreage:** `acres` is measured from the outline (in the equal-area CRS). `deed_acres` is what the assessor recorded; the layer gives 0 for about half the parcels (48%), stored as unknown. Where both exist (1 acre or more), half agree within 4% and three quarters within 9%.

### Owner names are personal information

The layer returns owner names (`OWNER`, `OWNER2`) and the property's street address with the outlines. They are split off on the way in:

- `parcels` holds outlines, IDs and acreage. It's served as map tiles.
- `parcel_owners` holds the owner names and property address, keyed by `gislink`. **The tile server never publishes it** (`martin/martin.yaml` lists tables one by one, with `auto_publish: false`), and `tests/unit/test_martin_config.py` fails if any map layer gets an owner, address or link column.
- Owner names reach only `shortlist.csv`, `all_corridors.csv` and `report.md` in `data/outputs/`, which stay on this computer (`data/` is never committed). `shortlist.geojson` has parcel IDs and acreage but no owner names, since GeoJSON is the file that gets passed around.
- The raw cache in `data/raw/tn_parcels/` holds owner names too, and is never committed either.

Don't paste owner names into docs, issues, screenshots or chat.

### Which parcels a corridor is on

Computed by two database views (migration 0010), so they always match the latest parcel ingest; there's no separate step to run.

- **Turbine parcel** (`corridor_turbine_parcels`): the parcel under the bottom of the stretch, where the turbine would go. On a shared property line the bigger parcel wins. If the point falls in a gap between parcels (a road, a river bed), it's the nearest parcel within 100 m, with its distance, flagged `turbine_outside_parcel`. Beyond 100 m there's no turbine parcel (`no_parcel_found`).
- **Parcels crossed** (`corridor_parcels`): every parcel the stretch touches, longest share first. Streams are often property lines, so a stretch running along one counts the parcels on **both** banks. That's on purpose: both owners would have a say in a diversion.
- **Minimum acreage (D-03, still open):** turbine parcels under the 10-acre placeholder (`config/parcels.yaml`) are flagged `parcel_under_min_acres`. Nothing is filtered or re-ranked on it yet.

On the five counties' latest runs, 4,779 of 4,919 ranked corridors have a turbine parcel; the 140 without all end outside the five counties (streams within 500 m of a county line are analyzed for both counties). Of the six streams that reach 1 kW, Negro Den Creek and the Elk River headwater share one 1,901-acre Grundy parcel, Big Creek sits on a 10,481-acre one and Bethel Creek on a 1,355-acre one. Parcels that large are often timber company, conservation or public land, which changes who to contact. Rogers Branch's turbine parcel is 76 acres (the stretch crosses 3 parcels) and the unnamed Marion stream's is 44 acres.
