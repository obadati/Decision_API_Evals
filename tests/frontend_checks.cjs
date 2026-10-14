// Behavioral checks that do not require a browser or a listening server.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const nodes = new Map();
function node(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {
    innerHTML: "", textContent: "", hidden: false, dataset: {},
    classList: { toggle() {} }, addEventListener() {},
  });
  return nodes.get(selector);
}
let resolveFetch;
const context = vm.createContext({
  console, structuredClone, setTimeout() {},
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  document: {
    querySelector: node, querySelectorAll() { return []; },
    body: { classList: { toggle() {} } }, addEventListener() {},
  },
  window: { addEventListener() {}, scrollTo() {} },
  location: { hash: "#history/old-run" },
  history: { replaceState() {} },
  fetch: () => new Promise(resolve => { resolveFetch = resolve; }),
});
const source = fs.readFileSync(path.join(__dirname, "../src/decision_tests/static/app.js"), "utf8");
vm.runInContext(source.replace(/\nboot\(\);\s*$/, "\n"), context);

(async () => {
  const pending = vm.runInContext('renderReport("old-run")', context);
  context.location.hash = "#prompts";
  node("#content").innerHTML = "prompt-library-content";
  resolveFetch({ ok: false, status: 404, json: async () => ({ detail: "Run not found" }) });
  await pending;
  assert.equal(node("#content").innerHTML, "prompt-library-content", "A stale report error must preserve later navigation");

  vm.runInContext(`
    state.demoOnly = true;
    state.draft.demo = false; // A rerun from a saved live report.
    state.providers = [{id:"openai",name:"OpenAI",model:"gpt-6-luna",has_key:false}];
    state.prompts = [{id:"test.yaml",name:"Test",prompt:"Choose yes",choices:[{value:"yes"},{value:"no"}],dataset:[{query:"hello",expected:"yes"}]}];
    renderRun();
  `, context);
  assert.equal(vm.runInContext("state.draft.demo", context), true);
  assert.match(node("#content").innerHTML, /id="demo"[^>]*checked[^>]*disabled/);
  assert.match(node("#demo-note").textContent, /zero API calls/);

  function comparisonWinnersFor(results) {
    context.comparisonFixture = results;
    return JSON.parse(vm.runInContext(`JSON.stringify(Object.fromEntries(
      Object.entries(comparisonWinners(comparisonFixture)).map(([metric, ids]) => [metric, [...ids]])
    ))`, context));
  }
  function providerResult(overrides = {}) {
    return {
      total_test_runs: 10,
      success_rate: 50,
      request_success_rate: 50,
      latency_ms: { successful: { min: 40, avg: 100, p90: 200 } },
      usage: { input_tokens: 100, output_tokens: 100 },
      usage_requests: { input_tokens: 10, output_tokens: 10 },
      estimated_cost_usd: 0.1,
      cost_complete: true,
      ...overrides,
    };
  }

  const rankingFixture = {
    alpha: providerResult({
      success_rate: 75, request_success_rate: 100,
      latency_ms: { successful: { min: 80, avg: 100, p90: 300 } },
      usage: { input_tokens: 500, output_tokens: 900 }, estimated_cost_usd: 0.04,
    }),
    beta: providerResult({
      success_rate: 90, request_success_rate: 90,
      latency_ms: { successful: { min: 20, avg: 50, p90: 200 } },
      usage: { input_tokens: 300, output_tokens: 500 }, estimated_cost_usd: 0.02,
    }),
    gamma: providerResult({
      success_rate: 90, request_success_rate: 80,
      latency_ms: { successful: { min: 20, avg: 50, p90: 200 } },
      usage: { input_tokens: 300, output_tokens: 200 }, estimated_cost_usd: 0.02,
    }),
  };
  const rankingBefore = structuredClone(rankingFixture);
  assert.deepEqual(comparisonWinnersFor(rankingFixture), {
    correct: ["beta", "gamma"],
    requests: ["alpha"],
    avg: ["beta", "gamma"],
    p90: ["beta", "gamma"],
    input: ["beta", "gamma"],
    min: ["beta", "gamma"],
    cost: ["beta", "gamma"],
  }, "each metric should rank in its own direction and preserve exact ties");
  assert.deepEqual(rankingFixture, rankingBefore, "winner calculation must not mutate provider results");

  const zeroFixture = { only: providerResult({
    success_rate: 0, request_success_rate: 0,
    latency_ms: { successful: { min: 0, avg: 0, p90: 0 } },
    usage: { input_tokens: 0, output_tokens: 0 },
    estimated_cost_usd: 0,
  }) };
  assert.deepEqual(comparisonWinnersFor(zeroFixture), {
    correct: [], requests: [], avg: [], p90: [],
    min: [], input: [], cost: [],
  }, "a single model has no comparative winner, including zero values");
  assert.deepEqual(comparisonWinnersFor({ zero: zeroFixture.only, positive: providerResult() }), {
    correct: ["positive"], requests: ["positive"], min: ["zero"], avg: ["zero"],
    p90: ["zero"], input: ["zero"], cost: ["zero"],
  }, "zero must remain a valid winner when another comparable value is worse");

  const crossingLatencyFixture = {
    fasterAverage: providerResult({ latency_ms: { successful: { min: 5, avg: 10, p90: 100 } } }),
    fasterTail: providerResult({ latency_ms: { successful: { min: 2, avg: 20, p90: 50 } } }),
  };
  const crossingLatencyWinners = comparisonWinnersFor(crossingLatencyFixture);
  assert.deepEqual(crossingLatencyWinners.min, ["fasterTail"], "minimum latency should rank independently");
  assert.deepEqual(crossingLatencyWinners.avg, ["fasterAverage"], "average latency should rank independently");
  assert.deepEqual(crossingLatencyWinners.p90, ["fasterTail"], "P90 latency should rank independently");

  const malformedFixture = {
    invalid: providerResult({
      success_rate: "100", request_success_rate: 101,
      latency_ms: { successful: { min: -1, avg: -1, p90: Infinity } },
      usage: { input_tokens: -1, output_tokens: "0" },
      estimated_cost_usd: NaN,
    }),
    valid: providerResult({
      success_rate: 10, request_success_rate: 0,
      latency_ms: { successful: { min: 1, avg: 1, p90: 1 } },
      usage: { input_tokens: 0, output_tokens: 0 }, estimated_cost_usd: 0,
    }),
  };
  assert.deepEqual(comparisonWinnersFor(malformedFixture), {
    correct: [], requests: [], avg: [], p90: [],
    min: [], input: [], cost: [],
  }, "invalid values cannot supply a competitor for the only usable value");

  const unavailableFixture = { unavailable: providerResult({
    success_rate: undefined, request_success_rate: "100",
    latency_ms: { successful: { min: undefined, avg: undefined, p90: Infinity } },
    usage: { input_tokens: -1, output_tokens: null },
    estimated_cost_usd: undefined,
  }) };
  assert.deepEqual(comparisonWinnersFor(unavailableFixture), {
    correct: [], requests: [], avg: [], p90: [], min: [], input: [], cost: [],
  }, "missing and invalid values must leave a metric without a winner");

  const incompleteFixture = {
    complete: providerResult({
      usage: { input_tokens: 50, output_tokens: 60 },
      usage_requests: { input_tokens: 10, output_tokens: 10 },
      estimated_cost_usd: 0.02, cost_complete: true,
    }),
    partial: providerResult({
      usage: { input_tokens: 1, output_tokens: 1 },
      usage_requests: { input_tokens: 9, output_tokens: 10 },
      estimated_cost_usd: 0.01, cost_complete: false,
    }),
  };
  assert.deepEqual(comparisonWinnersFor(incompleteFixture), {
    correct: [], requests: [], avg: [], p90: [],
    min: [], input: [], cost: [],
  }, "equal values and metrics with only one complete value must have no winner");

  const unequalWorkloadsFixture = {
    small: providerResult({
      total_test_runs: 2, usage: { input_tokens: 100, output_tokens: 100 },
      usage_requests: { input_tokens: 2, output_tokens: 2 }, estimated_cost_usd: 0.2,
    }),
    large: providerResult({
      total_test_runs: 3, usage: { input_tokens: 1, output_tokens: 1 },
      usage_requests: { input_tokens: 3, output_tokens: 3 }, estimated_cost_usd: 0.01,
    }),
  };
  const unequalWorkloadsWinners = comparisonWinnersFor(unequalWorkloadsFixture);
  assert.deepEqual(unequalWorkloadsWinners.input, [], "token totals cannot rank across unequal workloads");
  assert.deepEqual(unequalWorkloadsWinners.cost, [], "cost totals cannot rank across unequal workloads");
  assert.deepEqual(unequalWorkloadsWinners.avg, [], "equal latency values have no winner across workloads");

  for (const total of [0, -1, NaN, Infinity, "10"]) {
    const invalidWorkloadFixture = {
      first: providerResult({
        total_test_runs: total,
        usage_requests: { input_tokens: total, output_tokens: total },
      }),
      second: providerResult({
        total_test_runs: total,
        usage_requests: { input_tokens: total, output_tokens: total },
      }),
    };
    const invalidWorkloadWinners = comparisonWinnersFor(invalidWorkloadFixture);
    assert.deepEqual(invalidWorkloadWinners.input, [], `input totals cannot rank when workload ${String(total)} is invalid`);
    assert.deepEqual(invalidWorkloadWinners.cost, [], `cost totals cannot rank when workload ${String(total)} is invalid`);
  }

  const renderedReport = {
    provider_results: {
      openai: providerResult({
        model: "comparison-openai-model <img src=x onerror=unsafeModel()>", success_rate: 100, request_success_rate: 100,
        latency_ms: { successful: { min: 5, avg: 10, p90: 20 } },
        usage: { input_tokens: 200, output_tokens: 200 }, estimated_cost_usd: 0.01,
      }),
      azure: providerResult({
        model: "comparison-azure-model", success_rate: 80, request_success_rate: 80,
        latency_ms: { successful: { min: 10, avg: 20, p90: 30 } },
        usage: { input_tokens: 100, output_tokens: 100 }, estimated_cost_usd: 0.02,
      }),
    },
  };
  const renderedJob = { providers: [
    { id: "openai", name: "OpenAI </strong><script>unsafeName()</script>" },
    { id: "azure", name: "Microsoft Azure" },
  ] };
  context.comparisonJob = renderedJob;
  context.comparisonReport = renderedReport;
  const comparisonHtml = vm.runInContext("renderComparisonTable(comparisonJob, comparisonReport)", context);
  assert.match(comparisonHtml, /class="provider-logo"[^>]+OpenAI logo/);
  assert.match(comparisonHtml, /class="provider-logo"[^>]+Microsoft Azure logo/);
  assert.doesNotMatch(comparisonHtml, /<script>/, "provider names must be escaped");
  assert.match(comparisonHtml, /&lt;\/strong&gt;&lt;script&gt;/);
  assert.match(comparisonHtml, /&lt;script&gt;unsafeName\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(comparisonHtml, /<img src=x onerror=/, "model names must be escaped");

  assert.match(comparisonHtml, /<th scope="col">Min ms ↓<\/th>/);
  assert.doesNotMatch(comparisonHtml, /Output tokens|accuracy-track/);
  assert.match(comparisonHtml, /data-accuracy-band="green"/);
  assert.match(comparisonHtml, /data-accuracy-band="yellow"/);

  const tableRows = [...comparisonHtml.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)].slice(1);
  function cellsFor(model) {
    const row = tableRows.find(match => match[1].includes(model));
    assert.ok(row, `rendered comparison should include ${model}`);
    const cells = [...row[1].matchAll(/<td\b[^>]*>[\s\S]*?<\/td>/g)].map(match => match[0]);
    assert.equal(cells.length, 8, "comparison rows should contain provider plus seven metric cells");
    return cells;
  }
  function hasWinner(cell) { return /class="[^"]*\bwinner-cell\b/.test(cell); }
  function assertAccessibleCrown(cell, message) {
    assert.match(cell, /<svg\b[^>]*class="[^"]*\bwinner-crown\b[^"]*"/, `${message}: SVG crown is missing`);
    const wrapper = cell.match(/<span\b[^>]*>[\s\S]*?<svg\b[^>]*class="[^"]*\bwinner-crown\b[^"]*"[\s\S]*?<\/svg>[\s\S]*?<\/span>/)?.[0] || "";
    assert.match(wrapper, /role="img"/, `${message}: crown needs image semantics`);
    assert.match(wrapper, /aria-label="[^"]+"/, `${message}: crown needs an accessible label`);
  }
  const openaiCells = cellsFor("comparison-openai-model");
  const azureCells = cellsFor("comparison-azure-model");
  for (const index of [1, 2, 3, 4, 5, 7]) {
    assert.ok(hasWinner(openaiCells[index]), `OpenAI should win metric cell ${index}`);
    assertAccessibleCrown(openaiCells[index], `OpenAI metric cell ${index}`);
    assert.ok(!hasWinner(azureCells[index]), `Azure should not win metric cell ${index}`);
  }
  for (const index of [6]) {
    assert.ok(!hasWinner(openaiCells[index]), `OpenAI should not win metric cell ${index}`);
    assert.ok(hasWinner(azureCells[index]), `Azure should win metric cell ${index}`);
    assertAccessibleCrown(azureCells[index], `Azure metric cell ${index}`);
  }
  const comparisonBody = results => {
    context.comparisonReport = { provider_results: results };
    return vm.runInContext("renderComparisonTable(comparisonJob, comparisonReport)", context).match(/<tbody>([\s\S]*?)<\/tbody>/)[1];
  };
  for (const [value, band] of [[0, "red"], [50, "red"], [50.1, "orange"], [69.9, "orange"], [70, "yellow"], [88, "yellow"], [89.9, "yellow"], [90, "green"], [100, "green"]]) {
    const body = comparisonBody({ openai: providerResult({ success_rate: value }) });
    assert.match(body, new RegExp(`data-accuracy-band="${band}"`), `score ${value} should use ${band}`);
    assert.match(body, new RegExp(`class="accuracy-value">${value}%<`), "accuracy must stay visible as a percentage");
  }
  for (const [light, strong] of [[50, 0], [51, 69], [70, 89], [90, 100]]) {
    const backgroundFor = value => comparisonBody({ openai: providerResult({ success_rate: value }) }).match(/--accuracy-background:([^";]+)/)[1];
    assert.notEqual(backgroundFor(light), backgroundFor(strong), "color strength must vary within each band");
  }
  for (const invalid of [null, undefined, NaN, Infinity, -1, 101, "88"]) {
    const body = comparisonBody({ openai: providerResult({ success_rate: invalid }) });
    assert.doesNotMatch(body, /data-accuracy-band|--accuracy-background/, "unavailable accuracy must have neutral shading");
    assert.match(body, /class="accuracy-value">—</);
  }

  const equalBody = comparisonBody({ openai: providerResult(), azure: providerResult() });
  assert.equal((equalBody.match(/class="winner-cell\b/g) || []).length, 0, "all equal metric cells must have no winner highlight");
  assert.doesNotMatch(equalBody, /winner-crown/, "a column where every model ties must not show crowns");

  const subsetTieBody = comparisonBody({
    openai: providerResult({ success_rate: 90 }),
    azure: providerResult({ success_rate: 90 }),
    jev: providerResult({ success_rate: 80 }),
  });
  assert.equal((subsetTieBody.match(/winner-crown/g) || []).length, 0, "tied leaders must have no crown when another model scores lower");
  assert.equal((subsetTieBody.match(/class="winner-cell\b/g) || []).length, 2, "only the two tied leaders should have winner highlights");

  const singleBody = comparisonBody({ openai: providerResult() });
  assert.equal((singleBody.match(/class="winner-cell\b/g) || []).length, 0);
  assert.doesNotMatch(singleBody, /winner-crown/, "a single model has neither winner highlights nor crowns");
  console.log("Frontend behavior checks passed: stale navigation, pinned demo rerun, metric winners, and comparison rendering.");
})().catch(error => { console.error(error); process.exitCode = 1; });
