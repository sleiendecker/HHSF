# Hydro Analysis Assumptions

How `hhsf analyze hydro` turns each stream reach into head, firm flow and firm power, and every assumption behind those numbers. The judgment calls themselves live in [DECISIONS.md](DECISIONS.md); the values used are in [`config/hydro.yaml`](../config/hydro.yaml). Every run copies them into `analysis_runs.params`, so you can always see which assumptions produced a given result.

**Ground rule:** when a number can't be estimated reliably it is stored as **empty (NULL) with a flag** explaining why. The tool never fills a gap with a made-up value.

## What one row in `hydro_corridors` is

For now a **corridor is a whole perennial stream reach** (one NHDPlus HR flowline), so there is one row per reach per run. NHDPlus only records elevations at each reach's two ends, so finding the steepest 1,000 ft stretch inside a reach ([D-08](DECISIONS.md#d-08--what-counts-as-one-corridor-measure)) needs a terrain model (3DEP DEM), which isn't loaded yet. The table already has `from_measure` / `to_measure` (percent along the reach, 0 = downstream end) so a later version can store just a sub-stretch without changing the schema. Today they are always 0 and 100.

**Consequence:** head and power are for the *whole* reach, as if the penstock ran its full length. On long reaches (main stems especially) that overstates what a homestead system could capture. Treat the power numbers as "how much energy this reach has", not "what one penstock would deliver", until D-08 is built.

## Metrics

### Gross head (`gross_head_m`)

**Definition** ([site-selection.md B9](../site-selection.md)): the elevation drop between the reach's upstream and downstream ends, from NHDPlus HR **smoothed** elevations:

```
gross_head_m = max_elev_m − min_elev_m        (NHDPlus maxelevsmo − minelevsmo, cm → m)
```

- `head_method = nhdplus_vaa_smoothed_reach`
- Smoothing makes elevations never rise going downstream, so gross head is never negative. A flat reach has head **0** (29 reaches in Sequatchie), which is a real value, not missing data.
- If either elevation is missing: NULL, `head_flag = missing_elevation`. If the data says the stream rises downstream: NULL, `head_flag = elevation_rises_downstream`. (Neither happens in Sequatchie today.)
- Smoothed elevations come from the 10 m-era NHDPlus DEM processing, not survey. Expect errors of a few meters; a site visit (or later, 1 m LiDAR) confirms the real drop.

### Net head (`net_head_m`)

```
net_head_m = gross_head_m × 0.85              (D-07)
```

A planning allowance for pipe friction in a reasonably sized penstock. Real losses depend on pipe diameter and length and are worked out per site later.

### Reach length (`length_m`)

Copied from `streams.length_m`, which is measured in EPSG:5070 (equal-area, meters) per the CRS contract in `src/hhsf/core/crs.py`.

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
- The 1 kW target (D-01) is only reported in the run summary here. Filtering and scoring against it is the next phase.

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

## Runs

- Every `hhsf analyze hydro` writes a new `analysis_runs` row (`analysis = 'hydro'`) with the AOI, `git describe` code version, the data source versions, every assumption above (`params`), and a summary of counts. Its corridors carry that `run_id`; earlier runs are never changed.
- `status` is `running` while it works, `finished` when saved, `failed` if it crashed (with no corridors).
- To read the latest run: `SELECT * FROM hydro_corridors WHERE run_id = (SELECT max(run_id) FROM analysis_runs WHERE analysis = 'hydro' AND status = 'finished')`.
