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

**Changing the length:** set `corridor.penstock_length_ft` in `config/hydro.yaml`, or for one run, `uv run hhsf analyze hydro --penstock-ft 2000`. The D-08 sensitivity check on 2026-10-19 gave:

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
| No StreamStats Q90 (basin too small, service down, failed for this point, wrong basin, or switched off), and the reach drains to the AOI's reference gauge's basin | Gauge Q90 × (reach area ÷ gauge area) | `gauge_drainage_area_ratio` | `low` | the reason (e.g. `below_regression_min_drainage_area`), plus `area_ratio_outside_0.5_1.5` when it applies |
| Basin smaller than the regression's minimum drainage area (1.3 sq mi), not in the gauge's basin | **NULL** | — | `below_applicability` | `below_regression_min_drainage_area` |
| StreamStats down, failed for this point, delineated the wrong basin, or switched off, not in the gauge's basin | NHDPlus EROM **mean annual** flow | `nhdplus_erom` | `low` | the reason, plus `mean_flow_not_q90` |
| No drainage area or no mean flow | NULL | — | `unavailable` | the reason |

Notes:

- **Why "medium" and never "high":** the Tennessee flow-duration equations (Law and others, 2009, USGS SIR 2009-5159) have an average standard error of prediction of about **60% for D90**. The value is stored in `flow_std_error_pct`.
- **Minimum drainage area:** 1.3 sq mi (3.37 km²) for the Central and East Tennessee region (`GC1419`, which covers Sequatchie); 2.0 sq mi for West Tennessee (`GC1418`). StreamStats will happily extrapolate below the limit and only add a warning, so the tool checks the limit itself. Below it there is no flow estimate at all ([D-09](DECISIONS.md#d-09--small-streams-below-streamstats-applicability-limit-measure)): the corridor keeps its head and is marked `below_applicability` instead of being dropped.
- **In Sequatchie, 635 of 989 reaches (64%) are below the minimum.** Only 354 are big enough for the regression. That's the share D-09 said to measure. The reaches on the Sequatchie River side now get a gauge-transfer estimate (below); the Collins River side isn't covered yet and stays NULL.
- **The fallback overstates flow.** EROM mean annual flow is always higher than Q90, often by a large factor on small streams. It's used only so a corridor isn't left blank when StreamStats is down; the `low` confidence and `mean_flow_not_q90` flag mark it. Re-run once StreamStats is back to replace these values.
- **Wrong-basin check:** StreamStats snaps the pour point to its own stream grid. If the basin it delineates differs from the NHDPlus drainage area by more than 25%, it probably snapped to a different stream, so its estimate is discarded (`streamstats_drainage_area_mismatch`).
- **Pour point:** 30 m above the reach's downstream end (at most 10% of the reach), so StreamStats doesn't snap onto the confluence with the next reach. NHDPlus flowlines are drawn upstream to downstream; this was checked on the loaded data (every end-to-start connection goes to an equal or larger drainage area).
- `mean_flow_cfs` (EROM) is copied onto every row for reference, whatever method was used.

### Gauge transfer (drainage-area ratio)

When StreamStats can't give a Q90, a reach that drains to the AOI's reference gauge's basin borrows the gauge's measured Q90, scaled by drainage area:

```
Q90_reach = Q90_gauge × (drainage_area_reach ÷ drainage_area_gauge)
```

- **Gauge:** set per AOI in `config/aoi/<key>.yaml` (`reference_gauge`). For Sequatchie: **USGS 03571000, Sequatchie River near Whitwell, TN**, 402 sq mi, in the Sequatchie HUC8 basin (`06020004`).
- **Gauge Q90:** the 10th percentile of approved daily mean flows (NWIS, parameter 00060), water years 2002–2025 (`config/hydro.yaml`, `gauge_transfer`). For Whitwell: **77.2 cfs from 8,400 days**, or 0.19 cfs per sq mi. (Mean flow over the same period is 803 cfs, about 10× Q90, which shows how much the EROM mean-flow fallback overstates firm flow.) A gauge with less than 10 years of approved record is refused.
- **Which reaches:** only those whose downstream end is inside the gauge's HUC8 (from the Watershed Boundary Dataset): 522 of Sequatchie's 989. The other 467 drain to the Collins River (Cumberland basin, HUC8 `05130107`) and keep the StreamStats/EROM/NULL rules above. That basin has its own long-record gauge, Collins River near McMinnville (03421000, daily record since 1924), which could cover them the same way if the AOI allowed more than one reference gauge.
- **Confidence is always `low`.** The method assumes every square mile of the basin yields the same low flow. The usual guidance is to trust it for basins between half and one and a half times the gauge's size; every reach in the county is far smaller (largest ratio ≈ 0.17), so every transferred value also carries `area_ratio_outside_0.5_1.5`. `flow_reference_gauge` and `flow_drainage_area_ratio` record the gauge and ratio used.
- **Basins below the regression minimum** get a transferred value too, with `below_regression_min_drainage_area` kept in `flow_flags` so D-09 sites can still be picked out.
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

### Usable flow and firm power (`usable_flow_cfs`, `firm_power_w`)

```
usable_flow  = firm_flow × 0.5                       (D-05: half can be diverted; the rest stays in the stream)
firm_power_W = ρ · g · Q · H · η
             = 1000 kg/m³ × 9.81 m/s² × usable_flow (m³/s) × net_head (m) × 0.5   (D-06)
```

- 1 cfs = 0.0283168 m³/s.
- η = 0.5 is the whole system (turbine, generator, controller, transmission). With gross head it reproduces the D-01 rule of thumb, *watts ≈ head (ft) × flow (gpm) ÷ 10*; on top of that this tool also takes the 85% net-head and 50% diversion allowances, so its numbers are deliberately conservative.
- Put together, firm power ≈ **0.21 × ρ·g·Q90·H_gross**.
- `power_confidence` follows the flow: `medium` for StreamStats Q90, `low` for anything else, `unavailable` (NULL power) when flow or head is NULL.
- The 1 kW target (D-01) is only reported in the run summary here. Filtering and scoring against it happen in `hhsf score hydro`; see [SCORING.md](SCORING.md).

## StreamStats

Endpoints, limits and timeouts are in [`config/data_sources.yaml`](../config/data_sources.yaml) under `streamstats`. Each pour point takes four calls (USGS's published workflow):

1. `ss-delineate` — delineate the watershed (GET, ~3–4 s)
2. NSS `regressionregions/bylocation` — which regression region it's in (POST, ~3 s)
3. `ss-hydro` `basin-characteristics/calculate` — drainage area, recession index, climate factor, soil permeability (POST)
4. NSS `scenarios/estimate` — evaluate the D90 equation (POST)

- **Rate limit:** USGS asks for **no more than four simultaneous requests** and says enforcement may change. No per-second limit is published and responses carry no rate-limit headers. The tool runs at most 4 reaches at a time.
- **Caching:** every successful response is saved under `data/cache/streamstats/` (by step and pour point), so a re-run only asks for what it doesn't have. Failures aren't cached. Delete the folder to force fresh answers.
- **Outages:** a 5xx, 429 or timeout is retried twice (2 s, then 4 s). After 3 reaches in a row find the service down, the run stops calling it and the remaining reaches fall back to EROM, flagged `streamstats_unavailable`.
- **Status:** on 2026-10-18 steps 1, 2 and 4 worked but step 3 (`ss-hydro`) returned **502 Bad Gateway** all day. It was back on 2026-10-19, and its responses parse correctly (a list of `{code, value}` items). It's slow under load: delineations took up to ~15 s, and a full Sequatchie run took about 30 minutes (2026-10-19).
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
