/*
 * Client-side projection math. Runs in the browser (loaded by map.html as a
 * plain <script>, no build step) and under Node's built-in test runner (see
 * tests/test_estimate.js) - so no ES module syntax, no dependencies.
 *
 * Two ways an area's still-uncounted vote gets projected:
 *
 *   1. Historical-swing model, when a historical baseline exists for the
 *      area: compare the area's own counted-so-far D/R split to its
 *      historical (prior-cycle) split, get a swing, dampen that swing by how
 *      much of the state has actually reported (both by this area's own vote
 *      share and by how much of the STATE's expected vote the areas
 *      reporting so far actually represent - see confidenceWeight and
 *      expectedTotalVotes), and apply the dampened swing to the historical
 *      split for the remaining vote.
 *
 *   2. Flat fallback, when no historical baseline exists for the area (no
 *      seat-year match, non-county geography, redistricting-affected
 *      district - see races.py). Assumes the remaining vote splits exactly
 *      like the vote counted so far: Predicted = Real * 100 / PercentIn.
 *      This is the formula fetch_results.py used to compute server-side for
 *      every area; it now lives here as the honest floor when we have
 *      nothing better to go on.
 *
 * At 0% counted, neither model applies - there is nothing to extrapolate
 * from, so estimateArea returns predictions of null rather than guessing.
 */

// Full trust in the observed swing once this fraction of the state's votes
// are in - short of that, the swing is dampened proportionally.
const VOTE_TRUST_THRESHOLD = 0.5;
// Full trust once areas covering this fraction of the state's total expected
// vote (reporting + not-yet-reporting) have reported - guards against one
// large, unrepresentative county alone driving percentIn up. This is a
// fraction of actual vote weight, not a raw area count, because a single
// big-city county reporting alone can be more representative than five tiny
// rural counties - see expectedTotalVotes for how "the state's total
// expected vote" is known even for areas that haven't reported at all.
const AREA_VOTE_TRUST_THRESHOLD = 0.3;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// historicalEntry is whatever historical_<race>.json has for this area's
// FIPS/GEOID, or undefined/null if the area has none - see
// build_historical_baseline.py for which areas are absent and why (no
// seat-year match, non-county geography, redistricting drift).
function historicalShare(historicalEntry) {
  if (!historicalEntry) return null;
  const { demShare, repShare, year } = historicalEntry;
  if (!Number.isFinite(demShare) || !Number.isFinite(repShare)) return null;
  return { demShare, repShare, year };
}

// Vote-weighted Dem share of a state, from its counties' reference entries
// ([fips, entry] pairs, e.g. historical_president.json). Null if none.
function stateShareFromCounties(countyEntries) {
  let dem = 0, total = 0;
  for (const [, entry] of countyEntries) {
    if (!historicalShare(entry) || !(entry.votes > 0)) continue;
    dem += entry.demShare * entry.votes;
    total += entry.votes;
  }
  return total > 0 ? dem / total : null;
}

// A county baseline for a race that only has a statewide result (Senate):
// the county's own lean in a reference race (President), shifted by how much
// the statewide result ran ahead of or behind that reference race. Without
// this, every county would be compared against the state average, so a
// normally deep-blue or deep-red county would read as a huge "swing".
function shiftedCountyBaseline(countyReference, stateEntry, stateReferenceShare) {
  const county = historicalShare(countyReference);
  const state = historicalShare(stateEntry);
  if (!county || !state || stateReferenceShare == null) return null;
  const demShare = clamp(county.demShare + (state.demShare - stateReferenceShare), 0, 1);
  return { demShare, repShare: 1 - demShare, year: state.year };
}

// Deviation of the counted-so-far Dem share from the historical Dem share,
// in the same [-1, 1] units as demShare itself. Null if nothing is counted
// yet (nothing to compare) or there's no historical share to compare against.
function observedSwing(demReal, repReal, historical) {
  if (!historical) return null;
  const counted = demReal + repReal;
  if (!(counted > 0)) return null;
  return demReal / counted - historical.demShare;
}

// 0 (trust nothing yet, use the historical share as-is) to 1 (trust the
// observed swing fully) - the product of two independent checks so that
// neither this area's own percentIn nor a lopsided sample of the state's
// counties alone can force full confidence.
//
// reportingVoteFraction: fraction (0-1) of the state's total expected vote
// (reporting + not-yet-reporting, using historical totals for the latter -
// see expectedTotalVotes) accounted for by areas currently reporting. The
// caller computes this once per state, not per area - see map.html.
function confidenceWeight(percentIn, reportingVoteFraction) {
  return confidenceParts(percentIn, reportingVoteFraction).weight;
}

// The two factors of confidenceWeight, kept separate so the UI can show them.
function confidenceParts(percentIn, reportingVoteFraction) {
  const voteComponent = clamp((percentIn / 100) / VOTE_TRUST_THRESHOLD, 0, 1);
  const areaComponent = clamp(reportingVoteFraction / AREA_VOTE_TRUST_THRESHOLD, 0, 1);
  return { voteComponent, areaComponent, weight: voteComponent * areaComponent };
}

// An area's own best-guess eventual vote total: self-extrapolated from its
// own percentIn if it has reported anything (all-party totalVotes, unlike
// flatPredict's two-party total), otherwise the historical baseline's own
// past total as a stand-in for an area that hasn't reported at all yet. Null if neither is
// available (no live data AND no historical coverage) - genuinely unknown,
// not a number to guess at.
function expectedTotalVotes(area, historicalEntry) {
  if (area && area.percentIn > 0 && area.totalVotes) {
    return area.totalVotes * 100 / area.percentIn;
  }
  if (historicalEntry && Number.isFinite(historicalEntry.votes)) {
    return historicalEntry.votes;
  }
  return null;
}

// The Dem/Rep split to assume for the vote that hasn't been counted yet.
function projectedRemainingShare(historical, swing, weight) {
  const dem = clamp(historical.demShare + weight * swing, 0, 1);
  return { demShare: dem, repShare: 1 - dem };
}

// Splits remainingVotes (already known non-negative) by remainingShare and
// adds it to what's actually been counted.
function predictFromRemainingShare(demReal, repReal, remainingVotes, remainingShare) {
  return {
    demPredicted: demReal + remainingVotes * remainingShare.demShare,
    repPredicted: repReal + remainingVotes * remainingShare.repShare,
  };
}

// Today's flat extrapolation, kept as the fallback for areas with no
// historical baseline - identical to fetch_results.py's former
// `round(v * 100 / percent_in)`, computed here instead now that the math has
// moved client-side.
function flatPredict(demReal, repReal, percentIn) {
  const scale = 100 / percentIn;
  return { demPredicted: demReal * scale, repPredicted: repReal * scale };
}

/**
 * Project one area's full-count result.
 *
 * area: { demReal, repReal, percentIn, totalVotes } - no per-area
 *   totalExpected: NBC only publishes that at the state level, so an area's
 *   own remaining vote is derived from its own percentIn and D+R counted instead
 *   (identical to how the flat formula always worked).
 * historicalEntry: this area's entry from historical_<race>.json, or
 *   null/undefined if it has none.
 * reportingVoteFraction: fraction (0-1) of the state's total expected vote
 *   accounted for by areas currently reporting - the caller computes this
 *   once per state (see map.html), estimateArea doesn't compute it itself.
 *
 * Returns null predictions (noData: true) at 0% counted; the count itself
 * at 100%; a flat-fallback prediction (usedFallback: true) with no
 * historical entry; otherwise the full swing-blended prediction. Every
 * intermediate value is returned too, so the UI can show the exact working
 * rather than re-deriving it.
 */
function estimateArea(area, historicalEntry, reportingVoteFraction) {
  const { demReal, repReal, percentIn, totalVotes } = area;

  if (!(percentIn > 0) || !totalVotes) {
    return { demPredicted: null, repPredicted: null, noData: true, usedFallback: false };
  }

  if (percentIn >= 100) {
    return { demPredicted: demReal, repPredicted: repReal, noData: false, usedFallback: false };
  }

  const historical = historicalShare(historicalEntry);
  if (!historical) {
    const { demPredicted, repPredicted } = flatPredict(demReal, repReal, percentIn);
    return { demPredicted, repPredicted, noData: false, usedFallback: true, scale: 100 / percentIn };
  }

  const counted = demReal + repReal;
  const swing = observedSwing(demReal, repReal, historical);
  const { voteComponent, areaComponent, weight } = confidenceParts(percentIn, reportingVoteFraction);
  const remainingShare = projectedRemainingShare(historical, swing, weight);
  const remainingVotes = counted * (100 - percentIn) / percentIn;
  const { demPredicted, repPredicted } =
    predictFromRemainingShare(demReal, repReal, remainingVotes, remainingShare);

  return {
    demPredicted, repPredicted, noData: false, usedFallback: false,
    historical, swing, weight, remainingShare,
    countedDemShare: counted > 0 ? demReal / counted : null,
    voteComponent, areaComponent, reportingVoteFraction, remainingVotes,
  };
}

const api = {
  VOTE_TRUST_THRESHOLD,
  AREA_VOTE_TRUST_THRESHOLD,
  observedSwing,
  confidenceWeight,
  confidenceParts,
  expectedTotalVotes,
  projectedRemainingShare,
  flatPredict,
  estimateArea,
  stateShareFromCounties,
  shiftedCountyBaseline,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else {
  window.estimate = api;
}
