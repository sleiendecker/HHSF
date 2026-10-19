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

### D-09 · Small streams below StreamStats' applicability limit: **Measure**

Tennessee's flow equations have a minimum drainage area, and many good headwater sites fall below it.

Starting value: **keep them, rank on head only, mark as low confidence.** If they turn out to be a large share of the top candidates, consider a fallback estimate (e.g., scaling from a nearby gauged basin by drainage area), clearly flagged.

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
