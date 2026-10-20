# Decisions Log

Judgment calls that [site-selection.md](../site-selection.md) leaves open, with the default we'll use until someone says otherwise. Each one will become a value in versioned config (`config/scoring_profiles/*.yaml`, `config/aoi/*.yaml`), so changing a decision means editing config and re-running, not rewriting code.

## What the output is for

The tool produces a **filtered shortlist of candidate parcels to visit in person**, with owner contact details for follow-up. It doesn't pick a site. So scoring should favor *not missing* good sites over perfect ranking precision, and every site on the list should show why it made the cut and how confident the numbers are, so a visit can confirm or rule it out quickly.

**Status key**

- **Default**: chosen from physics or standard micro-hydro practice. Override if you disagree.
- **Measure**: has a starting value, but will be checked against the loaded Sequatchie data (see [Sensitivity check](#sensitivity-check)).
- **Owner**: only the two of you can answer this. The default is a placeholder.

**Blocking the first run:** nothing. D-01 and D-02 are decided; everything else has a usable default.

---

## Owner decisions

### D-01 · Household power target: **Decided**

How much continuous power the homestead needs. This anchors the hydro score: it decides what counts as "enough" and whether most streams qualify or almost none do.

| Target (continuous) | Daily energy | Typical profile |
| --- | --- | --- |
| 0.5 kW | 12 kWh/day | Efficient off-grid home, propane/wood for heat and cooking |
| 1.0 kW | 24 kWh/day | Comfortable off-grid home, some electric appliances |
| 1.2 kW | ~29 kWh/day | Average US household |
| 2.0 kW+ | 48+ kWh/day | Electric heat, workshop, EV charging |

What it takes at the stream (rule of thumb, ~50% system efficiency, see D-06): **watts ≈ head (ft) × flow (gpm) ÷ 10**

| Target | Example head × firm flow |
| --- | --- |
| 0.5 kW | 50 ft × 100 gpm, or 100 ft × 50 gpm |
| 1.0 kW | 100 ft × 100 gpm, or 200 ft × 50 gpm |
| 2.0 kW | 200 ft × 100 gpm, or 100 ft × 200 gpm |

(1 cfs ≈ 449 gpm, so 100 gpm ≈ 0.22 cfs.) Because of D-05, the stream's Q90 flow needs to be about **twice** the flow column above.

**Decision:** **1.0 kW continuous (24 kWh/day) as the initial minimum.** Corridors with estimated firm power below 1 kW drop off the shortlist; above it, more power scores higher (see D-11). Corridors whose flow can't be estimated reliably (D-09) stay on the list with a low-confidence marker instead of being dropped. It's a setting, so changing it later only means re-scoring. Sites need roughly 100 ft × 100 gpm usable, so about 200 gpm (~0.45 cfs) Q90 at 100 ft of head.

### D-02 · Search region and drive time: **Owner**

Which counties to include and the maximum drive time from Chattanooga. Only needed for the regional run; v0.1 is Sequatchie County alone.

Candidates from the plan: Hamilton, Sequatchie, Marion, Bledsoe, Rhea (TN); possibly Dade, Walker (GA); Jackson, DeKalb (AL). Hamilton needs its own parcel source, since it isn't in the TN statewide parcel layer.

**Decision:** **Within a 1-hour drive of Chattanooga.** The search region is the area inside that drive time, not a fixed list of counties; the counties to ingest are whichever ones that area touches.

- Drive time measured from **downtown Chattanooga** (placeholder origin; could be changed to a specific address).
- Needs a road network and a routing tool to build the 1-hour area (see the missing-data notes in site-selection.md §2.C). Sequatchie County, the v0.1 test county, sits mostly inside it.
- v0.1 stays Sequatchie-only; the 1-hour area is used from the regional run onward.

### D-03 · Minimum acreage, budget, privacy weight: **Owner** (not needed until parcels are added)

- Minimum usable acreage for a homestead: placeholder **10 acres**
- Rough budget ceiling (filters parcels later, via assessed value): placeholder **none**
- Privacy weight in the overall score (spec says 10%): placeholder **10%**

**Decision:** _\_\_\_\__

---

## Physics and practice defaults

### D-04 · Definition of "reliable" flow: **Default**

**Q90**: the flow the stream meets or beats 90% of the time, from the StreamStats flow-duration estimate. Micro-hydro sized on mean annual flow sits idle or under-produces for much of the dry season, so we use firm flow. Q95 is the more conservative alternative.

Open call (2026-10-19): when StreamStats outlines the wrong basin at a reach's outlet, its Q90 from a point further up the reach (scaled to the outlet by drainage area) is still treated as a StreamStats Q90 at **medium** confidence, flagged `streamstats_pour_point_moved`. The scaling step is small next to the regression's ~60% error. The alternative was to rank such sites below other StreamStats sites. See [ASSUMPTIONS.md](ASSUMPTIONS.md#when-streamstats-snaps-to-the-wrong-stream).

### D-05 · Fraction of stream flow that can be diverted: **Default**

**50% of Q90.** Permits usually require leaving water in the stream (a bypass flow). This is a planning placeholder, not a regulatory answer: TDEC ARAP permitting decides the real number site by site.

### D-06 · Overall system efficiency (η): **Default**

**50%** (water to delivered electricity: turbine, generator, controller and transmission combined). This is the common planning number for small systems and is what makes the head × gpm ÷ 10 rule work. Well-built Pelton systems can do better.

### D-07 · Net head as a share of gross head: **Default**

**85%.** Allows for pipe friction in a reasonably sized penstock. Real losses depend on pipe diameter and length, which come later in site-specific design.

---

## To be measured on real data

### D-08 · What counts as one "corridor": **Measure**

A whole stream reach is the wrong unit: the useful part is usually its steepest stretch. A corridor will be **the best head gain within a sliding penstock length** along a reach.

Starting value: **1,000 ft of penstock**. We'll compare 500, 1,000 and 2,000 ft. Penstock length is a major cost driver, so this trades power against cost.

**Built** (2026-10-19): `make analyze` now finds each reach's steepest stretch on the USGS 3DEP 10 m elevation model. Value in `config/hydro.yaml` (`corridor.penstock_length_ft`); method in [ASSUMPTIONS.md](ASSUMPTIONS.md#how-the-steepest-stretch-is-found). First comparison: 14 / 19 / 33 streams reach 1 kW at 500 / 1,000 / 2,000 ft (0 / 1 / 3 of them at medium confidence), against 66 for whole reaches. Once wrong-basin StreamStats answers were retried (2026-10-19, see D-04), mean-flow sites dropped out and it's **1 / 6 / 19** (still 0 / 1 / 3 at medium confidence). So the window length matters a lot here and is worth a real conversation.

Open calls made while building it (defaults, change in config or say so):

- **Penstock length is measured along the stream**, not as a straight pipe. Conservative on winding streams.
- **Elevations every 10 m** (about one per DEM cell, `corridor.sample_spacing_m`).
- **The profile is cleaned** so it never rises downstream, and **capped by NHDPlus's end elevations** for the reach, so a stretch never claims more drop than NHDPlus gives the whole reach.
- **Flow stays the reach's**, at its downstream end, even when the stretch is near the top. Slightly generous for stretches high on long reaches.
- **Ties go to the most downstream stretch** (more water there).
- **One stretch per reach.** A long reach with two good drops only gets its best one; adjacent reaches aren't joined into one stretch.

### D-09 · Small streams below StreamStats' applicability limit: **Measure**

Tennessee's flow equations have a minimum drainage area, and many good headwater sites fall below it.

Starting value: **keep them, rank on head only, mark as low confidence.** If they turn out to be a large share of the top candidates, consider a fallback estimate (e.g., scaling from a nearby gauged basin by drainage area), clearly flagged.

Measured (2026-10-19): they were. Before any fallback, 635 of 989 reaches were too small, and head-only corridors were 272 of the 278 ranked and 14 of the top 20.

**Decision (2026-10-19): give them a flagged, low-confidence flow.** Inside the reference gauge's basin, the gauge transfer (already built). Everywhere else, the **regional yield**: drainage area × the median StreamStats Q90 per square mile of the in-range StreamStats reaches in the same HUC8 (pooled with the HUC6, then HUC4, if the HUC8 has fewer than 10). Flagged `below_regression_min_drainage_area` plus `regional_yield_basin:<code>`, `flow_method = regional_streamstats_yield`, confidence `low`. They are then scored on power like any other corridor; `--no-regional-yield` brings back the head-only ranking.

- **Why not more gauges:** Collins River near McMinnville runs 10× StreamStats, like Whitwell (both basins include spring-fed limestone). The Walden Ridge gauges disagree with each other from zero to 0.087 cfs per sq mi and have no 10-year recent record. The regional yield matches StreamStats where both exist (leave-one-out median ratio 1.0) and agrees with the measured plateau-only gauges. Comparison in [ASSUMPTIONS.md](ASSUMPTIONS.md#regional-low-flow-yield).
- **Result:** all 296 small basins outside the Whitwell basin now have a flow, so no corridor is head-only. None of them reaches 1 kW (best 56 W), so they are filtered out. The ranked list is the 6 corridors with power ≥ 1 kW; the 5 low-tier ones are small basins on the Whitwell gauge transfer.
- **Open call:** small basins inside the Whitwell basin still use the gauge transfer, about 10× the regional yield for the same size of basin. On the regional yield the five low-tier sites would drop to roughly 100–150 W. Switching them over (or ranking gauge-transfer small basins as "unconfirmed") is the next decision.
- **Springs aren't captured** by any regional method. A visit is the only way to find a spring-fed headwater.
- **Open call (owner, touches D-01):** D-01 says corridors whose flow can't be estimated reliably stay on the list. These now have a low-confidence estimate under 1 kW and drop off it. To keep the high-head small streams visible for spring-hunting, run `uv run hhsf analyze hydro --no-regional-yield`, or filter `hydro_scores` for `below_regression_min_drainage_area` in `flow_flags`.

### D-10 · Rainfall in the score: **Measure**

The plan predicts rainfall is nearly constant across one county. We'll sample PRISM normals across Sequatchie to confirm. Starting value: **leave it out of the v0.1 hydro score** (flow from StreamStats already reflects rainfall); revisit for the multi-county run.

### D-11 · Score scale breakpoints: **Measure**

Scores will be absolute (a site's score never changes because another site was added), anchored on D-01:

- Firm power: below the D-01 minimum (1 kW) the corridor is **filtered out**, not scored. Sub-score **50 at 1 kW, 100 at ≥ 3 kW**, linear between. Placeholder.
- Head sub-score: 0 below **10 ft** (too low for practical small turbines), 100 at **≥ 200 ft**. Placeholder.
- Hydro score = **70% power, 30% head**. Head gets some weight on its own because it makes sites usable even when flow estimates are uncertain (D-09).

Breakpoints get adjusted after looking at the real spread of head and power in Sequatchie.

---

## Sensitivity check

After the first full run, re-rank corridors under each alternative and report how much the **top 20** changes:

| Decision | Alternatives tested |
| --- | --- |
| D-04 | Q90 vs. Q95 |
| D-05 | 30% / 50% / 70% diversion |
| D-08 | 500 / 1,000 / 2,000 ft penstock window |
| D-09 | Keep flagged vs. exclude sub-threshold basins |
| D-11 | Power/head weighting 70/30 vs. 50/50 vs. 100/0 |

Decisions that barely move the top 20 keep their default and stop being discussed. Ones that reshuffle it get a real conversation.

---

## Change log

| Date | Decision | Change |
| --- | --- | --- |
| 2026-10-18 | All | Initial defaults |
| 2026-10-18 | D-01 | Power target set to 1.0 kW continuous (provisional, pending owner) |
| 2026-10-18 | D-01 | Confirmed as 1.0 kW initial minimum (shortlist filter) |
| 2026-10-18 | D-02 | Search region: 1-hour drive from Chattanooga |
| 2026-10-18 | — | Added "What the output is for": a shortlist for in-person visits and owner contact |
| 2026-10-19 | D-08 | Built: steepest 1,000 ft stretch per reach on 3DEP; open calls listed under D-08 |
| 2026-10-19 | D-04 | Wrong-basin StreamStats answers retried further up the reach; no corridor uses mean flow any more. Open call listed under D-04 |
| 2026-10-19 | D-09 | Small basins outside the gauge's basin get a regional low-flow yield (median StreamStats Q90 per sq mi of their HUC8), low confidence. No corridor is head-only any more; none of them reaches 1 kW. Open call listed under D-09 |
