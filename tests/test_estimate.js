// Pure-math unit tests for estimate.js. Run with `node --test tests/`.
// No browser, no Selenium - see tests/README.md for why the rest of this
// suite needs a real browser and this file doesn't.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  confidenceWeight,
  observedSwing,
  projectedRemainingShare,
  expectedTotalVotes,
  flatPredict,
  estimateArea,
} = require("../estimate.js");

test("confidenceWeight is 0 with nothing counted", () => {
  assert.equal(confidenceWeight(0, 0), 0);
});

test("confidenceWeight reaches full trust at 50% in with enough of the state's vote accounted for", () => {
  assert.equal(confidenceWeight(50, 0.3), 1);
  assert.equal(confidenceWeight(100, 1), 1); // never exceeds 1
});

test("confidenceWeight dampens proportionally below the vote threshold", () => {
  assert.equal(confidenceWeight(10, 0.3), 0.2); // 10/50
});

test("confidenceWeight dampens an unrepresentative sample of the state's vote (hole #5)", () => {
  // 30% of THIS area's own vote is in either way, but in one case the
  // counties reporting so far only account for 5% of the STATE's total
  // expected vote (a small, possibly unrepresentative sample); in the other
  // they account for 30% (a much more substantial slice).
  const smallSample = confidenceWeight(30, 0.05);
  const largeSample = confidenceWeight(30, 0.3);
  assert.ok(smallSample < largeSample);
  assert.equal(largeSample, 0.6); // 30/50 * 1 (vote fraction already at threshold)
  assert.ok(smallSample < 0.6);
});

test("observedSwing is null with nothing counted", () => {
  assert.equal(observedSwing(0, 0, { demShare: 0.5, repShare: 0.5 }), null);
});

test("observedSwing is null with no historical baseline", () => {
  assert.equal(observedSwing(10, 5, null), null);
});

test("observedSwing matches history exactly when the counted split equals it", () => {
  const historical = { demShare: 0.2, repShare: 0.8 };
  assert.equal(observedSwing(20, 80, historical), 0);
});

test("projectedRemainingShare clamps to [0,1]", () => {
  const historical = { demShare: 0.9, repShare: 0.1 };
  const share = projectedRemainingShare(historical, 0.5, 1); // 0.9 + 0.5 = 1.4, clamp to 1
  assert.equal(share.demShare, 1);
  assert.equal(share.repShare, 0);
});

test("expectedTotalVotes self-extrapolates from an area's own percentIn when it has reported", () => {
  const area = { percentIn: 25, totalVotes: 500 };
  assert.equal(expectedTotalVotes(area, { votes: 999999 }), 2000); // 500 * 100/25, ignores historical
});

test("expectedTotalVotes falls back to the historical total when nothing has reported yet", () => {
  assert.equal(expectedTotalVotes(null, { votes: 42000 }), 42000);
  assert.equal(expectedTotalVotes({ percentIn: 0, totalVotes: 0 }, { votes: 42000 }), 42000);
});

test("expectedTotalVotes is null with neither live data nor a historical baseline", () => {
  assert.equal(expectedTotalVotes(null, null), null);
  assert.equal(expectedTotalVotes({ percentIn: 0, totalVotes: 0 }, null), null);
});

test("flatPredict matches the original server-side formula exactly", () => {
  // Was `round(v * 100 / percent_in)` in fetch_results.py before the math
  // moved client-side; no rounding here since intermediate math shouldn't
  // round early, but the ratio must be identical.
  const { demPredicted, repPredicted } = flatPredict(300, 200, 25);
  assert.equal(demPredicted, 1200); // 300 * 100/25
  assert.equal(repPredicted, 800);
});

test("estimateArea returns no prediction at 0% counted, even with a historical baseline", () => {
  const area = { demReal: 0, repReal: 0, percentIn: 0, totalVotes: 0 };
  const result = estimateArea(area, { demShare: 0.51, repShare: 0.49 }, 0);
  assert.equal(result.noData, true);
  assert.equal(result.demPredicted, null);
  assert.equal(result.repPredicted, null);
});

test("estimateArea returns the exact real counts at 100% counted", () => {
  const area = { demReal: 5100, repReal: 4900, percentIn: 100, totalVotes: 10000 };
  const result = estimateArea(area, { demShare: 0.2, repShare: 0.8 }, 1);
  assert.equal(result.demPredicted, 5100);
  assert.equal(result.repPredicted, 4900);
});

test("estimateArea falls back to the flat formula with no historical baseline", () => {
  const area = { demReal: 300, repReal: 200, percentIn: 25, totalVotes: 500 };
  const result = estimateArea(area, null, 0.3);
  assert.equal(result.usedFallback, true);
  assert.equal(result.demPredicted, 1200);
  assert.equal(result.repPredicted, 800);
});

// Worked scenarios: swing agreeing vs. contradicting history, at 10% and
// 50% counted, mirroring the design discussion. 1,000,000 expected votes,
// historical baseline D 51% / R 49%, and the state's reporting counties
// assumed to already account for the full expected vote (reportingVoteFraction
// of 1) so these isolate the vote-share (percentIn) component of confidence.
function scenario(percentInPct, demShareCounted, reportingVoteFraction) {
  const totalExpected = 1_000_000;
  const totalVotes = Math.round(totalExpected * (percentInPct / 100));
  const demReal = Math.round(totalVotes * demShareCounted);
  const repReal = totalVotes - demReal;
  const area = { demReal, repReal, percentIn: percentInPct, totalVotes };
  return estimateArea(area, { demShare: 0.51, repShare: 0.49 }, reportingVoteFraction);
}

test("swing agreeing with history: 10% in nudges the projection only slightly", () => {
  const result = scenario(10, 0.56, 1); // counted split D56/R44, S=+0.05
  // weight = confidenceWeight(10,1) = 0.2 -> dampened swing = 0.01
  // remaining share = 0.51 + 0.01 = 0.52
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.524) < 0.001);
});

test("swing agreeing with history: 50% in fully trusts the observed split", () => {
  const result = scenario(50, 0.55, 1); // weight = 1 -> remaining share = counted share
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.55) < 0.001);
});

test("swing contradicting history: 10% in barely moves the projection, not to the counted split", () => {
  const result = scenario(10, 0.35, 1); // counted split D35/R65, S=-0.16
  // weight = 0.2 -> dampened swing = -0.032 -> remaining share = 0.478
  const total = result.demPredicted + result.repPredicted;
  const demFraction = result.demPredicted / total;
  assert.ok(demFraction > 0.35, "should not fully believe a small early sample");
  assert.ok(demFraction < 0.51, "should still move away from pure history");
  assert.ok(Math.abs(demFraction - 0.465) < 0.001);
});

test("swing contradicting history: 50% in, sustained, flips the projected leader", () => {
  const result = scenario(50, 0.38, 1); // weight = 1 -> fully trusted, statewide leader flips
  assert.ok(result.demPredicted < result.repPredicted);
  const total = result.demPredicted + result.repPredicted;
  assert.ok(Math.abs(result.demPredicted / total - 0.38) < 0.001);
});
