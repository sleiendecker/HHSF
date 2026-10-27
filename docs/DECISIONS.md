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

**Built (PART 16, 2026-10-20).** The 1-hour area is computed by Valhalla (a routing engine run locally) on OpenStreetMap roads, by car with no traffic, from Miller Park downtown (`config/drive_time.yaml`). It touches 20 counties; by share inside: Catoosa GA 100%, Dade GA 89%, Hamilton 85%, Whitfield GA 77%, Bradley 70%, Marion 58%, Sequatchie 55%, Walker GA 49%, DeKalb AL 16%, Rhea 12%, Gordon GA 11%, Jackson AL 8%, McMinn 5%, Grundy 5%, Murray GA 4%, Bledsoe 4%, and slivers (1% or less) of Polk, Franklin, Meigs and Coffee. `make drive-area` prints the current list. Of the 4,919 ranked stretches in the five analyzed counties, 1,266 end inside it; of the six 1 kW streams, only Rogers Branch (54 min) and Marion's unnamed stream (48 min) do. Details: [ASSUMPTIONS.md](ASSUMPTIONS.md#roads-and-drive-time).

- **Decided (owner, 2026-10-20: "whatever is in the reqs"): flagged only, for now.** The requirements put drive time in the **Logistics** score (10% of the overall score, FR-7) and road access in the **Buildability** score (20%, FR-4), not in the hydro score. Neither exists yet: buildability also needs slope and septic soils, and the overall score also needs agriculture and privacy. So drive time and road distance stay columns and flags (`outside_drive_area`, `nearest_road_not_public`), and nothing is dropped or re-ranked on them. The 1-hour area decides which counties to load, not which streams to keep: dropping stretches outside it would remove four of the six 1 kW streams over an edge that assumes no traffic and a placeholder origin. When the overall score is built, drive time becomes its Logistics part and road distance part of Buildability.
- **Built (PART 19, 2026-10-21):** the overall score now uses drive time as its Logistics part and the nearest public road as part of Buildability, ranking corridors within each confidence tier (D-15). Still not a filter.
- **Built (PART 24, 2026-10-27):** Bradley (TN parcel layer) and Hamilton (the county's own `Live_Parcels` service) are analyzed. Neither has a 1 kW stream: Bradley lists 933 parcels (696 within 60 minutes, best hydro score 16), Hamilton 1,324 (1,154 within 60 minutes, best 53, all low-flow estimates). The shortlist now holds 4,947 parcels.
- **Open (owner):** which counties to add next. The biggest unanalyzed shares are the north Georgia counties (need Georgia parcel and flood sources, and D-19 and D-20). Counties with under ~10% inside may not be worth a full ingest.

### D-03 · Minimum acreage, budget, privacy weight: **Owner** (not needed until parcels are added)

- Minimum usable acreage for a homestead: placeholder **10 acres**
- Rough budget ceiling (filters parcels later, via assessed value): placeholder **none**
- Privacy weight in the overall score (spec says 10%): placeholder **10%** (used since `hydro_v6`, D-17)

Parcels are loaded now (PART 14). Until this is decided, the 10-acre placeholder (`config/parcels.yaml`) only **flags** small turbine parcels in the exports (`parcel_under_min_acres`); nothing is filtered or re-ranked.

**Decision:** _\_\_\_\__

---

### D-12 · Flood zones in the score: **Decided**

FEMA flood zones are loaded (PART 15). Should a turbine end or stretch in a flood zone count against a site?

- **Flag only.** The exports mark `turbine_in_floodway`, `turbine_in_sfha` and `stretch_in_sfha` (and `flood_zone_not_mapped`); nothing is filtered or re-ranked.
- Why not filter or penalize: a run-of-river intake and turbine nearly always sit beside the creek, so being in its floodplain is normal and mostly means a floodplain permit and flood-proofing the powerhouse. The house is what has to stay out of the floodplain, and that's chosen on the visit. Also, most small creeks were never studied by FEMA (see [ASSUMPTIONS.md](ASSUMPTIONS.md#flood-zones)), so a penalty would mostly hit the bigger, better-mapped streams.
- It also changes little where it matters: none of the six 1 kW streams has its turbine end in a flood zone, and dropping turbine ends in a floodway would remove 13 of 5,233 ranked stretches.
- Considered and not chosen: dropping corridors whose turbine end is in a **floodway**, or a small score penalty for one in the 100-year floodplain.

**Decision (2026-10-20):** flag only. Flood zones neither filter nor re-rank corridors. Revisit them for the **house site** instead, when a later step looks at buildable land (slope, soils, flood): does the parcel have enough ground outside the floodplain to build on?

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

### D-13 · What counts as buildable ground: **Default**

PART 17 (FR-4, buildability) needs three thresholds the requirements don't give. Starting values, in `config/buildability.yaml`:

- **Slope: 15% or less.** Where NRCS's soil ratings for houses without basements and for septic fields turn Very limited. 10% would be stricter (a flat pad and drain field); 20–25% is buildable with grading.
- **Septic: the Tennessee rating, Slightly or Moderately limited.** SSURGO's Tennessee rule approximates the state's conventional septic rules; the national rule is far stricter here (bedrock within 100 cm alone rules a soil out) and would leave most plateau parcels with nothing. Very limited and Not rated soils don't count.
- **Smallest patch: half an acre**, for a house, a drain field with its replacement area, and a driveway.
- Ground in FEMA's 100-year floodplain doesn't count (the house-site check D-12 asked for).

What it does on the five counties (2026-10-21): soils are the main limit. Of 654,000 acres of turbine parcels, 56% is gently sloped but only 12% is also on septic-suitable soils. 1,700 of 4,919 ranked stretches have no buildable ground on their turbine parcel. Of the six 1 kW streams, Rogers Branch and Marion's unnamed stream have none; Bethel Creek's parcel has 2.6 acres and the Negro Den Creek / Elk River parcel 2.0, a third of a mile to 1¾ miles from the turbine ends; Big Creek's 10,481-acre parcel has 917.

Not a filter: the counts and `no_buildable_ground` remove nothing. Since PART 19 buildable ground and road access make up the Buildability part (20%) of the overall score that ranks each tier (D-15). Details: [ASSUMPTIONS.md](ASSUMPTIONS.md#buildable-ground).

### D-14 · What counts as farmland: **Default**

PART 18 (FR-5, agriculture) needs thresholds the requirements don't give. Starting values, in `config/farmland.yaml`:

- **Farmland: NRCS prime farmland, plus farmland of statewide importance.** Tennessee designates no statewide farmland in these five counties, so here it's prime only. Soils that are prime only if drained or protected from flooding are counted apart and not as farmland.
- **Cropland: land capability class 1–3** (non-irrigated). Ground that can be tilled year after year with at most moderate care (contour rows, cover crops, some drainage), enough for a big garden, an orchard or a few acres of row crops. Class 4 can be cropped only occasionally and is hay and pasture in practice; counting it (`max_capability_class: 4`) adds 69,000 acres to the 236,000 on the turbine parcels.
- **Pasture: the main soil's best grazing yield**, in animal-unit months per acre a year, times its acres. SSURGO has no pasture suitability rating; its yields are the closest thing, and they cover steep and stony ground that isn't cropland.
- **Wet ground: somewhat poorly drained or wetter.**
- **No slope or floodplain test:** capability classes already reflect slope and flooding, and floodplain bottomland is often the best farmland. Every acre of the turbine parcel counts.

What it does on the five counties (2026-10-21): 13% of the turbine parcels' 654,000 acres is prime farmland and 36% is class 1–3. 311 of the 4,919 ranked stretches have neither on their turbine parcel. Of the six 1 kW streams, Rogers Branch and Marion's unnamed stream have none; Bethel Creek's and the Negro Den Creek / Elk River parcels have about 28 acres of class 3 ground each (2 acres prime on the latter); Big Creek's parcel has 923 acres prime and 4,705 class 1–3.

Not a filter: the acres and `no_farmland` remove nothing. Since PART 19 they make up the Agriculture part (20%) of the overall score that ranks each tier (D-15). Details: [ASSUMPTIONS.md](ASSUMPTIONS.md#farmland).

### D-15 · The overall score (FR-7): **Decided** (privacy and ranking), **Default** (curves)

FR-7 gives the weights: Hydro 40%, Buildability 20%, Agriculture 20%, Privacy 10%, Logistics 10%. PART 19 builds it as `hydro_v4` (`config/scoring_profiles/hydro_v4.yaml`, explained in [SCORING.md](SCORING.md#the-overall-score-fr-7)).

Decided by the owner (2026-10-21):

- **Privacy is left out until FR-6 is built**, and the other four weights rescale (44 / 22 / 22 / 11). Not a made-up neutral score.
- **The overall score re-ranks the shortlist within each confidence tier.** The hydro gate (1 kW, 10 ft) and the tiers stay as they are, so the overall score can't lift a low-confidence site above a medium one. The hydro score stays visible.

Defaults (change them in a new profile):

- **Missing data is left out, not scored as zero**, the same way: a factor or part with no data drops out and the rest rescale. Measured zeros (no buildable ground, no farmland) count as zeros. This keeps "never fill a gap with a made-up value" ([ASSUMPTIONS.md](ASSUMPTIONS.md)), at the cost of letting a corridor with no turbine parcel be scored on less.
- **Buildability** (D-13, plus road access, D-02): buildable acres 50% (½ acre → 40, 5 acres → 100), distance to the nearest of it 25% (on it → 100, ¼ mile → 50, 1 mile → 0), distance to a public road 25% (500 ft → 100, 1 mile → 0).
- **Agriculture** (D-14): prime (and statewide) farmland acres 40% (5 → 70, 20 → 100), class 1–3 cropland acres 30% (10 → 60, 40 → 100), pasture AUM 30% (60 → 50, 240 → 100). Acres are absolute, so a huge parcel tops out easily; that's fine for a homestead, which needs a few good acres, not a share.
- **Logistics** (D-02): drive minutes from downtown Chattanooga, 30 → 100, 60 → 70, 120 → 0.
- **The hydro part of a low-flow corridor** extends the power curve below the target (0 W → 0, 1 kW → 50), so its order still feels its power.

What it does (2026-10-21): Big Creek (Grundy) moves from #3 to #1 on its land, Bethel Creek passes Marion's unnamed stream, and Rogers Branch stays #1 in Sequatchie. Details in SCORING.md.

### D-16 · Farmland that's already open: **Default**

PART 20 adds USGS NLCD 2021 land cover ([ASSUMPTIONS.md](ASSUMPTIONS.md#land-cover)), and profile `hydro_v5` (the default until `hydro_v6`, D-17) uses it: the agriculture part gains a fourth factor, **cropland soils (class 1–3) that are open now** (pasture, hay, crops or grassland), 0 → 0, 5 acres → 60, 20 acres → 100. The other three keep their curves and drop to prime farmland 30%, cropland 20%, pasture 20%, so good soil under forest still counts, for less. `hydro_v4` is unchanged and still available.

Why: 79% of the turbine parcels' ground is forest, and only 27% of their cropland soils are open. Clearing woods for a field is real money and years of work, so a parcel with ten open acres on good soil is worth more to a homestead than one with a hundred acres of the same soil under trees.

What it does (2026-10-21): the six 1 kW streams keep their order (all their parcels are about 99% forest; Negro Den Creek's agriculture part drops from 64 to 43). The median agriculture part across ranked stretches drops from 70 to 63, and low-flow stretches move an average of 44 places.

### D-17 · Privacy (FR-6): **Default**

PART 21 builds FR-6 ([ASSUMPTIONS.md](ASSUMPTIONS.md#privacy)) and profile `hydro_v6` (now the default) gives it its 10% of the overall score (D-15 left it out until now). The defaults, all in `config/privacy.yaml` and the profile:

- **Where:** a home site, the turbine parcel's buildable ground nearest the turbine end (the turbine end where there's none). Not the most secluded spot on the parcel: the house goes near the turbine, and picking the best spot of a 900-acre parcel would flatter big parcels.
- **What's a building:** a Microsoft footprint of 400 sq ft or more, not on the turbine parcel (buildings on it would be yours).
- **The part** (each factor a curve, like the others): other buildings within 1/4 mile, 35% (0 → 100, 2 → 70, 5 → 40, 15 → 0); the nearest one, 15% (200 ft → 0, 1/8 mile → 50, 1/4 mile → 80, 1/2 mile → 100; none within 2 miles → 100); buildings per square mile within 1 mile, 20% (5 → 100, 25 → 60, 100 → 0); feet of woods toward the nearest public road, 20% (0 → 0, 200 → 70, 500 → 100); the nearest public road from the home site, 10% (100 ft → 0, 500 → 50, 1/4 mile → 100).

Why: neighbors close by are what most homesteaders mean by privacy, so the 1/4-mile count carries the most; density says whether the area is filling in; road visibility is about being seen from the road, so the woods between count twice as much as the distance. The curves sit on the five counties' medians (1 building within 1/4 mile, 25 per square mile, nearest 1,160 ft, 575 ft of woods) so a typical site scores about 70. The road distance pulls the other way from buildability's (a near road is easy access but less private); that's the real trade-off, and privacy is only 10%.

What it does (2026-10-21): the six 1 kW streams keep their order. Their privacy: Negro Den Creek and the Elk River 100, Bethel Creek 93, Big Creek 85, Rogers Branch 56, Marion's unnamed stream 51, so each one's overall score rises 1–5 points. Low-flow stretches move an average of 66 places. `hydro_v5` is unchanged and still available.

### D-18 · Subdivision feasibility (FR-9): **Default**

PART 22 builds FR-9 ([ASSUMPTIONS.md](ASSUMPTIONS.md#subdivision-feasibility)): whether a homestead piece could be split off the turbine parcel, and a 0–100 Subdivision Feasibility Score. The defaults, all in `config/subdivision.yaml`:

- **The piece:** the stretch, a house site and a driveway to a public road, all on the turbine parcel alone. The house site is privacy's home site (the buildable ground nearest the turbine end). Not the best piece the parcel could give: one simple, explainable cut, so big parcels aren't flattered.
- **Can it be cut** (`homestead_cut`): public road frontage of **50 ft** or more (TIGER, on the parcel or within 50 ft of its edge); **90%** or more of the stretch on the parcel; buildable ground for the house; and the turbine end, the house site and the frontage on one part of the parcel, so the wire and driveway needn't cross a neighbor. Driveway and wire lengths are straight lines.
- **The piece's size:** a 100 ft strip along the stretch, the wire and the driveway, plus a 2-acre house lot, trimmed to the parcel and rounded up to **5 acres**: Tennessee leaves tracts of 5 acres or more with no new road out of subdivision review (TCA 13-3-401).
- **The score:** a quarter each for frontage (up to 50 ft), the share of the stretch on the parcel, buildable acres (up to 2), and access (full for a driveway up to 1/4 mile, none at a mile, or when the house site can't reach the frontage). A part with no data is left out.
- **Not in the overall score.** FR-7 lists hydro, buildability, agriculture, privacy and logistics, not this; it's its own column and flags.

Why: frontage and an undivided route from the road to the house to the turbine are what make a parcel splittable at all; 50 ft is the usual minimum for a flag lot where a county has no rule of its own. The 90% leaves room for a stretch that runs along the property line (both banks count within the 50 ft tolerance) or clips a corner. A piece of 5 acres or more skips the county's plat approval in Tennessee, which is the simplest kind of split a seller would agree to.

What it does (2026-10-22): a piece can be cut from three of the six 1 kW streams' parcels: Big Creek (100), Negro Den Creek and the Elk River (one parcel, 75 each: a 1.2-mile driveway). Bethel Creek (50) and Rogers Branch (24) have no public road frontage; Marion's unnamed stream (74) has frontage but no buildable ground.

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

### D-09 · Small streams below StreamStats' applicability limit: **Decided**

Tennessee's flow equations have a minimum drainage area, and many good headwater sites fall below it.

Starting value: **keep them, rank on head only, mark as low confidence.** If they turn out to be a large share of the top candidates, consider a fallback estimate (e.g., scaling from a nearby gauged basin by drainage area), clearly flagged.

Measured (2026-10-19): they were. Before any fallback, 635 of 989 reaches were too small, and head-only corridors were 272 of the 278 ranked and 14 of the top 20.

**Decision (owner, 2026-10-19): every small basin gets the regional low-flow yield, and streams under the power target on it stay listed.**

1. **Regional yield everywhere.** A basin below 1.3 sq mi gets its drainage area × the median StreamStats Q90 per square mile of the in-range StreamStats reaches in the same HUC8 (the HUC6, then HUC4, pooled if the HUC8 has fewer than 10). That includes small basins in the Whitwell gauge's basin, which used to get the gauge transfer (about 10× higher). Flagged `below_regression_min_drainage_area` plus `regional_yield_basin:<code>`, `flow_method = regional_streamstats_yield`, confidence `low`. The gauge transfer stays for larger reaches StreamStats can't answer for. Since PART 13 the regional yield also replaces the NHDPlus mean-flow fallback on larger reaches outside the gauge's basin (wrong basin, or across the state line), which ran about 100× a Q90.
2. **Low-flow streams stay on the list.** A corridor under 1 kW on a low-confidence flow is ranked in a `low_flow` tier, last, by power, instead of being filtered out, so D-01's rule that corridors without a reliable flow stay listed still holds. Scoring profile `hydro_v2` (see [SCORING.md](SCORING.md)). Corridors under 1 kW on a medium-confidence StreamStats Q90 are still filtered out.

`uv run hhsf analyze hydro --no-regional-yield` brings back the starting rule (gauge transfer in its basin, head only elsewhere).

- **Why not more gauges:** Collins River near McMinnville runs 10× StreamStats, like Whitwell (both basins include spring-fed limestone). The Walden Ridge gauges disagree with each other from zero to 0.087 cfs per sq mi and have no 10-year recent record. The regional yield matches StreamStats where both exist (leave-one-out median ratio 1.0) and agrees with the measured plateau-only gauges. Comparison in [ASSUMPTIONS.md](ASSUMPTIONS.md#regional-low-flow-yield).
- **Result (hydro run 36, score run 37):** all 634 small basins with a drainage area have a flow, so no corridor is head-only. None reaches 1 kW; the best is Stillhouse Branch at 146 W. Rogers Branch (StreamStats, 1.3 kW) is the only corridor at or above 1 kW; the five plateau-edge streams that passed on the gauge transfer are now ranks 2–6 of 575 low-flow corridors at 105–146 W.
- **Springs aren't captured** by any regional method. The low-flow tier is the list to check on visits: a spring-fed headwater with lots of head could still work.

### D-10 · Rainfall in the score: **Measure**

The plan predicts rainfall is nearly constant across one county. We'll sample PRISM normals across Sequatchie to confirm. Starting value: **leave it out of the v0.1 hydro score** (flow from StreamStats already reflects rainfall); revisit for the multi-county run.

### D-11 · Score scale breakpoints: **Measure**

Scores will be absolute (a site's score never changes because another site was added), anchored on D-01:

- Firm power: below the D-01 minimum (1 kW) the corridor gets **no score**: it is filtered out on a medium-confidence flow, or listed last in the `low_flow` tier on a low-confidence one (D-09). Sub-score **50 at 1 kW, 100 at ≥ 3 kW**, linear between. Placeholder.
- Head sub-score: 0 below **10 ft** (too low for practical small turbines), 100 at **≥ 200 ft**. Placeholder. Since profile `hydro_v3` (2026-10-20) a corridor below 10 ft of net head is filtered out in every tier, not only head-only and low-flow: in Marion County the Sequatchie River itself made 1.2 kW on 1 ft of head.
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
| 2026-10-27 | D-02 | Added Bradley and Hamilton (PART 24); Hamilton's parcels from the county's own map service. No 1 kW stream in either |
| 2026-10-22 | D-18 | New default (PART 22): subdivision feasibility (FR-9). A homestead piece can be cut with 50 ft of public road frontage, 90% of the stretch on the turbine parcel, buildable ground, and turbine end, house site and frontage on one part of the parcel; 0–100 score, a quarter each for frontage, stretch, buildable acres and driveway length. Not in the overall score |
| 2026-10-21 | D-17 | New default (PART 21, profile `hydro_v6`): privacy gets its 10%: buildings within 1/4 mile of the home site 35%, the nearest 15%, density within 1 mile 20%, woods toward the nearest public road 20%, that road's distance 10% |
| 2026-10-21 | D-16 | New default (PART 20, profile `hydro_v5`): the agriculture part counts class 1–3 cropland that's open now (NLCD 2021) at 30%, prime farmland 30%, cropland 20%, pasture 20% |
| 2026-10-21 | D-15 | Owner: the FR-7 overall score (PART 19, profile `hydro_v4`) re-ranks each confidence tier; privacy is left out with the other weights rescaled until FR-6 is built. Default sub-score curves; missing data left out, never scored as zero |
| 2026-10-21 | D-14 | New default (PART 18): farmland is NRCS prime farmland (plus statewide importance, none here) and cropland is capability class 1–3; pasture from SSURGO grazing yields. Whole turbine parcel, no slope or floodplain test. Flagged only, never filtered or re-ranked |
| 2026-10-21 | D-13 | New default (PART 17): buildable ground is slope 15% or less, outside the 100-year floodplain, on soils not Very limited for septic (Tennessee rating), in half-acre patches. Flagged only, never filtered or re-ranked |
| 2026-10-20 | D-02 | Built the 1-hour drive area (PART 16): touches 20 counties. Owner deferred to the requirements: drive time and road distance are flagged only until the overall score (FR-7) gives them its Logistics (10%) and Buildability (20%) parts |
| 2026-10-20 | D-12 | Decided by the owner: FEMA flood zones (PART 15) are flagged only, never filtered or re-ranked; revisit for the house site with the buildable-land step |
| 2026-10-20 | D-03 | Owner: parcels loaded (PART 14); minimum acreage only flags, filters nothing until D-03 is decided. Owner names stay out of map tiles and the repo, in a separate table read only by the local exports |
| 2026-10-18 | All | Initial defaults |
| 2026-10-18 | D-01 | Power target set to 1.0 kW continuous (provisional, pending owner) |
| 2026-10-18 | D-01 | Confirmed as 1.0 kW initial minimum (shortlist filter) |
| 2026-10-18 | D-02 | Search region: 1-hour drive from Chattanooga |
| 2026-10-18 | — | Added "What the output is for": a shortlist for in-person visits and owner contact |
| 2026-10-19 | D-08 | Built: steepest 1,000 ft stretch per reach on 3DEP; open calls listed under D-08 |
| 2026-10-19 | D-04 | Wrong-basin StreamStats answers retried further up the reach; no corridor uses mean flow any more. Open call listed under D-04 |
| 2026-10-20 | D-09 | Owner: the regional yield also replaces the mean-flow fallback on larger reaches with no StreamStats answer (PART 13) |
| 2026-10-20 | D-11 | Owner: the 10 ft net-head minimum applies to every tier (profile `hydro_v3`, PART 13) |
| 2026-10-19 | D-09 | Decided by the owner: every small basin gets a regional low-flow yield (median StreamStats Q90 per sq mi of its HUC8), replacing the gauge transfer on small basins; corridors under 1 kW on a low-confidence flow stay listed in a `low_flow` tier (profile `hydro_v2`) |
