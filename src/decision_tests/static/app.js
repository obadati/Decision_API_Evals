"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const count = value => value == null ? "—" : Number(value).toLocaleString();
const percent = value => value == null ? "—" : `${Number(value).toFixed(1)}%`;
const money = value => value == null ? "—" : `$${Number(value).toFixed(8)}`;
const date = value => value ? new Date(value).toLocaleString(undefined, {month:"short",day:"numeric",year:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";
const relativeDate = value => {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  return minutes < 1 ? "Just now" : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : date(value);
};
const providerBrands = {
  jev: {name:"TypeSafe AI",logo:"https://raw.githubusercontent.com/typesafe-ai/typesafe-ai.github.io/main/logo.svg"},
  openai: {name:"OpenAI",logo:"https://cdn.openai.com/brand/OpenAI-black-monoblossom.svg"},
  azure: {name:"Microsoft Azure",logo:"https://raw.githubusercontent.com/gilbarbara/logos/main/logos/microsoft-azure.svg"},
};
const providerLogo = id => {
  const brand = providerBrands[id];
  return brand ? `<img class="provider-logo" src="${escape(brand.logo)}" alt="${escape(brand.name)} logo" width="56" height="56" decoding="async" referrerpolicy="no-referrer">` : "";
};
const badge = (status, text = status) => `<span class="badge ${escape(status)}">${escape(text)}</span>`;
const pathFor = (section, id) => `#${section}${id ? `/${encodeURIComponent(id)}` : ""}`;
let savedDraft = {};
try { savedDraft = JSON.parse(sessionStorage.getItem("decision-draft") || "{}"); } catch { /* use defaults */ }
const state = {
  providers: [], prompts: [], runs: [], active: null, banner: null, pending: false, demoOnly: false,
  route: "", dirty: false, editor: null, historyFilter: "all", search: "", report: null,
  notified: new Set(), tracked: sessionStorage.getItem("decision-tracked"),
  draft: {providers:["jev","openai","azure"],prompt_id:"",repetitions:1,concurrency:3,timeout:30,demo:false,...savedDraft},
};

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options, headers: options.body ? {"Content-Type":"application/json",...options.headers} : options.headers,
    cache:"no-store",
  });
  let data;
  try { data = await response.json(); } catch { throw new Error("The server returned an unreadable response."); }
  if (!response.ok) {
    const detail = data.detail ?? data.error;
    const message = Array.isArray(detail) ? detail.map(item => item.msg || "Invalid field").join(". ") : detail;
    throw new Error(typeof message === "string" ? message : `Request failed (${response.status}).`);
  }
  return data;
}

function toast(message, error = false, link = null) {
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.setAttribute("role", error ? "alert" : "status");
  node.innerHTML = `<span class="toast-icon">${error ? "!" : "✓"}</span><div>${escape(message)}${link ? `<a href="${escape(link)}">View results ↗</a>` : ""}</div><button class="toast-close" aria-label="Dismiss notification">×</button>`;
  $(".toast-close", node).onclick = () => node.remove();
  $("#toasts").append(node);
  setTimeout(() => node.remove(), error ? 16000 : 12000);
}

function showError(error, target = "#form-error") {
  const node = $(target);
  if (node) { node.hidden = false; node.textContent = error.message || String(error); node.scrollIntoView({block:"nearest",behavior:"smooth"}); }
  else toast(error.message || String(error), true);
}

function metrics() {
  const rows = state.prompts.reduce((total, prompt) => total + prompt.dataset.length, 0);
  return `<div class="overview-metrics"><div class="overview-metric"><span class="metric-icon">▤</span><div><strong>${state.prompts.length}</strong><small>prompts in your library</small></div></div><div class="overview-metric"><span class="metric-icon">◈</span><div><strong>${state.providers.length}</strong><small>decision providers</small></div></div><div class="overview-metric"><span class="metric-icon">≋</span><div><strong>${count(rows)}</strong><small>labeled dataset rows</small></div></div></div>`;
}

function selectedPrompt() { return state.prompts.find(prompt => prompt.id === state.draft.prompt_id) || state.prompts[0]; }
function totalRequests() { return (selectedPrompt()?.dataset.length || 0) * state.draft.repetitions * state.draft.providers.length; }

function renderRun() {
  if (state.demoOnly) state.draft.demo = true;
  const prompt = selectedPrompt();
  if (prompt) state.draft.prompt_id = prompt.id;
  $("#content").innerHTML = `<section class="hero"><div class="hero-copy"><div class="eyebrow">DECISION API BENCHMARKS</div><h1>Better decisions.<br><span>Start with evidence.</span></h1><p>One dataset. Every model. Compare accuracy, latency,<br>and cost to find the right fit for your next decision.</p><div class="hero-tags"><span>Compare models</span><span>Measure performance</span></div></div><div class="hero-visual"><img src="/static/decision-api-office.webp" alt="Professionals collaborating in a modern office with a blue decision API branching visualization" width="1536" height="1024" fetchpriority="high"><span class="hero-image-caption">HUMAN INSIGHT. MEASURABLE INTELLIGENCE.</span></div></section>
  ${metrics()}<div class="run-grid"><section class="panel"><div class="panel-head"><div><h2>Build your benchmark</h2><p>A repeatable test starts with the right setup.</p></div><span class="step">01 — SETUP</span></div><form id="run-form" class="panel-body"><div class="section-label">Select models <a href="#config">Configure providers ↗</a></div><div class="provider-options">${state.providers.map(provider => `<label class="provider-option"><input type="checkbox" name="provider" value="${escape(provider.id)}" ${state.draft.providers.includes(provider.id) ? "checked" : ""} aria-label="Include ${escape(provider.name)}">${providerLogo(provider.id)}<strong>${escape(provider.name)}</strong><small title="${escape(provider.model)}">${escape(provider.model || "No model set")}</small><span class="provider-state${provider.has_key ? "" : " missing"}"><span class="status-dot"></span>${provider.has_key ? "Key configured" : "Needs configuration"}</span></label>`).join("")}</div>
  <div class="field"><label for="run-prompt">Prompt to evaluate</label><select id="run-prompt" name="prompt_id" required>${state.prompts.length ? state.prompts.map(item => `<option value="${escape(item.id)}" ${item.id === prompt?.id ? "selected" : ""}>${escape(item.name)}</option>`).join("") : '<option value="">Create a prompt first</option>'}</select></div><div id="prompt-facts" class="prompt-facts"></div>
  <div class="field-grid"><div class="field"><label for="repetitions">Repetitions / row</label><input id="repetitions" type="number" name="repetitions" min="1" max="10000" step="1" required value="${escape(state.draft.repetitions)}"><small>Repeat each example</small></div><div class="field"><label for="concurrency">Concurrency</label><input id="concurrency" type="number" name="concurrency" min="1" max="1000" step="1" required value="${escape(state.draft.concurrency)}"><small>Across all models</small></div><div class="field"><label for="timeout">Timeout (seconds)</label><input id="timeout" type="number" name="timeout" min="0.1" max="3600" step="0.1" required value="${escape(state.draft.timeout)}"><small>Per request</small></div></div>
  <div class="divider"></div><div class="run-controls"><label class="switch-label"><input id="demo" type="checkbox" name="demo" ${state.draft.demo ? "checked" : ""} ${state.demoOnly ? "disabled" : ""}>Offline demo</label><button class="button primary" id="start-run" type="submit">Run benchmark <span>↗</span></button></div><div id="run-estimate" class="run-estimate"></div><p id="demo-note" class="demo-note"></p><div id="form-error" class="form-error" role="alert" hidden></div></form></section>
  <aside class="stack"><section class="panel preview-panel"><div class="panel-head"><h2>Inside this run</h2><span class="eyebrow">THE PROMPT</span></div><div class="panel-body" id="run-preview"></div></section><section class="panel"><div class="panel-head"><h2>Recent activity</h2><a href="#history" class="text-button">View all ↗</a></div><div class="panel-body" id="recent-runs"></div></section></aside></div>`;
  $("#run-form").addEventListener("input", updateDraft);
  $("#run-form").addEventListener("change", updateDraft);
  $("#run-form").addEventListener("submit", startRun);
  updateRunPreview(); renderRecent();
}

function updateDraft() {
  const form = $("#run-form");
  if (!form) return;
  state.draft = {
    providers: $$("input[name=provider]:checked", form).map(input => input.value),
    prompt_id: $("#run-prompt").value, repetitions:Number($("#repetitions").value),
    concurrency:Number($("#concurrency").value), timeout:Number($("#timeout").value), demo:$("#demo").checked,
  };
  sessionStorage.setItem("decision-draft", JSON.stringify(state.draft));
  updateRunPreview();
}

function updateRunPreview() {
  if (!$("#run-preview")) return;
  const prompt = selectedPrompt();
  $("#prompt-facts").innerHTML = prompt ? `<span>${prompt.choices.length} choices</span><span>${prompt.dataset.length} dataset rows</span><a href="${pathFor("prompts",prompt.id)}">Open in editor ↗</a>` : "";
  $("#run-preview").innerHTML = prompt ? `<p class="prompt-preview">${escape(prompt.prompt)}</p><div class="preview-pills">${prompt.choices.map(choice => `<span class="pill">${escape(choice.value)}</span>`).join("")}</div><div class="flow"><div class="flow-step"><b>▤</b>YOUR DATASET</div><span class="flow-arrow">→</span><div class="flow-step"><b>◈</b>${state.draft.providers.length} MODELS</div><span class="flow-arrow">→</span><div class="flow-step"><b>▥</b>CLEAR RESULTS</div></div>` : `<p class="empty-small">Create your first prompt to start comparing models.</p><a class="button secondary small" href="#prompts/new">Create a prompt +</a>`;
  $("#run-estimate").textContent = `${count(totalRequests())} requests · ${prompt?.dataset.length || 0} rows × ${state.draft.repetitions || 0} repetitions × ${state.draft.providers.length} models`;
  $("#demo-note").textContent = state.draft.demo ? "Synthetic responses, zero API calls. Demo results demonstrate the workflow." : "Live mode sends your dataset to the selected providers and incurs their API charges.";
  $("#start-run").disabled = !!state.active || state.pending || !prompt || !state.draft.providers.length;
  $("#start-run").innerHTML = state.pending ? "Starting…" : state.active ? "Benchmark in progress" : "Run benchmark <span>↗</span>";
}

async function startRun(event) {
  event.preventDefault();
  if (state.pending || state.active) return;
  updateDraft(); state.pending = true; updateRunPreview();
  $("#form-error").hidden = true;
  try {
    const job = await api("/runs",{method:"POST",body:JSON.stringify(state.draft)});
    state.active = job; state.banner = job; state.tracked = job.id;
    sessionStorage.setItem("decision-tracked", job.id);
    state.runs = [job,...state.runs.filter(run => run.id !== job.id)];
    updateCounts(); renderBanner(); renderRecent();
    toast(`Benchmark started. ${count(job.total)} requests queued.`);
  } catch (error) { showError(error); }
  finally { state.pending = false; updateRunPreview(); }
}

function renderRecent() {
  const target = $("#recent-runs");
  if (!target) return;
  target.innerHTML = state.runs.slice(0,3).map(job => `<a class="recent-row" href="${pathFor("history",job.id)}"><div class="recent-icon">${job.status === "completed" ? "✓" : job.status === "running" ? "◷" : "!"}</div><div class="recent-info"><strong>${escape(job.prompt_name)}</strong><small>${escape(relativeDate(job.started_at))} · ${job.providers.length} models</small></div>${badge(job.status)}</a>`).join("") || `<p class="empty-small">Your first benchmark is a fresh start.<br>Completed runs will appear here.</p>`;
}

function renderLibrary() {
  $("#content").innerHTML = `<div class="page-heading"><div><div class="eyebrow">THE FOUNDATION OF A FAIR TEST</div><h1>Prompt library</h1><p>A little structure. A lot of signal. Edit your instructions, choices, and labeled examples.</p></div><a href="#prompts/new" class="button primary">New prompt <span>+</span></a></div><div class="toolbar"><input class="search" id="prompt-search" type="search" placeholder="Search your prompts…" aria-label="Search prompts"><span class="save-note">${state.prompts.length} prompts in your library</span></div><div id="library-grid" class="library-grid"></div>`;
  $("#prompt-search").addEventListener("input", renderLibraryCards);
  renderLibraryCards();
}

function renderLibraryCards() {
  const search = ($("#prompt-search")?.value || "").toLowerCase();
  const prompts = state.prompts.filter(prompt => `${prompt.name} ${prompt.prompt}`.toLowerCase().includes(search));
  $("#library-grid").innerHTML = prompts.map(prompt => `<a class="panel prompt-card" href="${pathFor("prompts",prompt.id)}"><div class="prompt-card-top"><span class="document-icon">▤</span><span class="badge">CHOICE</span></div><h2>${escape(prompt.name)}</h2><p>${escape(prompt.prompt)}</p><div class="prompt-card-footer"><span>${prompt.choices.length} choices</span><span>${prompt.dataset.length} examples</span><span>↗</span></div></a>`).join("") || `<div class="empty-state"><div class="empty-icon">▤</div><h2>${search ? "No matching prompts" : "Your prompt library starts here"}</h2><p>${search ? "Try a different name or phrase." : "Create a prompt, define the choices, and add a few labeled examples."}</p>${search ? "" : '<a href="#prompts/new" class="button primary">Create a prompt +</a>'}</div>`;
}

function choiceRow(choice, index) {
  return `<div class="editor-row choice-row"><span class="row-number">${String(index + 1).padStart(2,"0")}</span><div class="field"><label>Choice value</label><input class="choice-value" value="${escape(choice.value)}" data-previous="${escape(choice.value)}" placeholder="e.g. billing" required aria-label="Choice ${index+1} value"></div><div class="field"><label>Description</label><input class="choice-description" value="${escape(choice.description)}" placeholder="What this choice means" required aria-label="Choice ${index+1} description"></div><button type="button" class="remove-button" data-action="remove-choice" aria-label="Remove choice ${index+1}">×</button></div>`;
}

function expectedOptions(choices, expected) {
  const values = choices.map(choice => choice.value).filter(Boolean);
  return `<option value="" ${!expected ? "selected" : ""}>Select expected choice</option>${values.includes(expected) || !expected ? "" : `<option selected value="${escape(expected)}">${escape(expected)} (removed choice)</option>`}${values.map(value => `<option value="${escape(value)}" ${value === expected ? "selected" : ""}>${escape(value)}</option>`).join("")}`;
}

function datasetRow(row, index, choices) {
  return `<div class="editor-row dataset-row"><span class="row-number">${String(index + 1).padStart(2,"0")}</span><div class="field"><label>Query to evaluate</label><textarea class="dataset-query" rows="2" placeholder="Write a sample input…" required aria-label="Example ${index+1} query">${escape(row.query)}</textarea></div><div class="field"><label>Expected choice</label><select class="dataset-expected" required aria-label="Example ${index+1} expected choice">${expectedOptions(choices,row.expected)}</select></div><button type="button" class="remove-button" data-action="remove-dataset" aria-label="Remove example ${index+1}">×</button></div>`;
}

function renderEditor(id) {
  const prompt = id === "new" ? {name:"",type:"choice",prompt:"",choices:[{value:"yes",description:"Matches the criteria"},{value:"no",description:"Does not match the criteria"}],dataset:[{query:"",expected:""}]} : state.prompts.find(item => item.id === id);
  if (!prompt) { renderMissing("This prompt could not be found.","#prompts"); return; }
  state.editor = structuredClone(prompt);
  $("#content").innerHTML = `<a class="back-link" href="#prompts">← Back to prompt library</a><form id="prompt-form" class="editor-layout"><div class="page-heading"><div><div class="eyebrow">PROMPT EDITOR</div><h1>${id === "new" ? "A new point of view." : escape(prompt.name)}</h1><p>Make every choice clear. Give every example an expected result.</p></div><button class="button primary" type="submit" id="save-prompt">Save prompt <span>↗</span></button></div><section class="panel editor-section"><div class="panel-head"><div><h2>Instructions</h2><p>The context shared across all selected providers.</p></div><span class="step">01</span></div><div class="panel-body"><div class="field"><label for="prompt-name">Prompt name</label><input id="prompt-name" name="name" value="${escape(prompt.name)}" placeholder="e.g. Customer support routing" required></div><div class="field"><label for="prompt-instructions">What should the model decide?</label><textarea id="prompt-instructions" name="prompt" rows="5" placeholder="Describe the task and how to select the best choice…" required>${escape(prompt.prompt)}</textarea></div></div></section>
  <section class="panel editor-section"><div class="panel-head"><div><h2>Choices <span id="choice-count" class="badge">${prompt.choices.length}</span></h2><p>Define unique labels and describe when each applies.</p></div><button class="button secondary small" type="button" data-action="add-choice">Add choice +</button></div><div class="panel-body" id="choice-rows">${prompt.choices.map(choiceRow).join("")}</div></section>
  <section class="panel editor-section"><div class="panel-head"><div><h2>Dataset <span id="dataset-count" class="badge">${prompt.dataset.length}</span></h2><p>Example queries and their correct answers. Multiline text is supported.</p></div><button class="button secondary small" type="button" data-action="add-dataset">Add example +</button></div><div class="panel-body" id="dataset-rows">${prompt.dataset.map((row,index) => datasetRow(row,index,prompt.choices)).join("")}</div></section><div id="form-error" class="form-error" role="alert" hidden></div><div class="editor-footer"><span class="save-note">${id === "new" ? "Saved as a new YAML file in your prompt directory." : `Changes save to ${escape(prompt.id)}.`}</span><div class="button-row"><a class="button secondary" href="#prompts">Cancel</a><button class="button primary" type="submit">Save prompt <span>↗</span></button></div></div></form>`;
  const form = $("#prompt-form");
  form.addEventListener("input", () => { state.dirty = true; });
  form.addEventListener("change", event => {
    state.dirty = true;
    if (event.target.classList.contains("choice-value")) {
      refreshExpected(event.target.dataset.previous, event.target.value);
      event.target.dataset.previous = event.target.value;
    }
  });
  form.addEventListener("click", event => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    state.dirty = true;
    const action = button.dataset.action;
    if (action === "add-choice") $("#choice-rows").insertAdjacentHTML("beforeend", choiceRow({value:"",description:""}, $$(".choice-row").length));
    if (action === "add-dataset") $("#dataset-rows").insertAdjacentHTML("beforeend", datasetRow({query:"",expected:""}, $$(".dataset-row").length, readChoices()));
    if (action === "remove-choice") { button.closest(".editor-row").remove(); refreshExpected(); }
    if (action === "remove-dataset") button.closest(".editor-row").remove();
    $("#choice-count").textContent = $$(".choice-row").length;
    $("#dataset-count").textContent = $$(".dataset-row").length;
    $$("#choice-rows .row-number").forEach((node,index) => { node.textContent = String(index+1).padStart(2,"0"); });
    $$("#dataset-rows .row-number").forEach((node,index) => { node.textContent = String(index+1).padStart(2,"0"); });
  });
  form.addEventListener("submit", savePrompt);
}

function readChoices() { return $$(".choice-row").map(row => ({value:$(".choice-value",row).value.trim(),description:$(".choice-description",row).value.trim()})); }
function refreshExpected(oldValue, newValue) {
  const choices = readChoices();
  $$(".dataset-expected").forEach(select => {
    const current = select.value;
    const expected = oldValue && current === oldValue ? newValue : current;
    select.innerHTML = expectedOptions(choices, expected);
  });
}

async function savePrompt(event) {
  event.preventDefault();
  const form = $("#prompt-form");
  if (form.dataset.saving) return;
  const body = {name:$("#prompt-name").value.trim(),type:"choice",prompt:$("#prompt-instructions").value,choices:readChoices(),dataset:$$(".dataset-row").map(row => ({query:$(".dataset-query",row).value,expected:$(".dataset-expected",row).value}))};
  if (body.choices.length < 2 || body.choices.length > 255) { showError(new Error("Define between 2 and 255 choices.")); return; }
  if (!body.dataset.length) { showError(new Error("Add at least one dataset example.")); return; }
  form.dataset.saving = "true"; $$("button[type=submit]",form).forEach(button => { button.disabled = true; });
  try {
    const id = state.editor.id;
    const result = await api(id ? `/prompts/${encodeURIComponent(id)}` : "/prompts", {method:id ? "PUT" : "POST",body:JSON.stringify(body)});
    const data = await api("/prompts"); state.prompts = data.prompts; state.dirty = false; updateCounts();
    toast("Prompt saved. Your next run will use these changes.");
    if (location.hash === pathFor("prompts",result.id)) renderEditor(result.id);
    else location.hash = pathFor("prompts",result.id);
  } catch (error) { showError(error); }
  finally { delete form.dataset.saving; $$("button[type=submit]",form).forEach(button => { button.disabled = false; }); }
}

function renderConfig() {
  $("#content").innerHTML = `<div class="page-heading"><div><div class="eyebrow">ONE WORKSPACE. YOUR MODELS.</div><h1>Configuration</h1><p>Connect your decision providers. Save once, use them in every benchmark.</p></div><span class="local-badge"><span class="status-dot"></span>Saved locally to .env</span></div><form id="config-form"><div class="config-grid">${state.providers.map(provider => `<section class="panel config-card" data-provider="${escape(provider.id)}"><div class="panel-head"><div class="provider-title">${providerLogo(provider.id)}<div><h2>${escape(provider.id === "jev" ? "TypeSafe AI" : provider.id === "openai" ? "OpenAI" : "Microsoft Azure")}</h2><p>Decision API provider</p></div></div>${badge(provider.has_key ? "completed" : "demo",provider.has_key ? "CONNECTED" : "SETUP")}</div><div class="panel-body"><div class="field"><label for="name-${provider.id}">Display name</label><input id="name-${provider.id}" name="name" value="${escape(provider.name)}" required maxlength="200"></div><div class="field"><label for="model-${provider.id}">${provider.id === "azure" ? "Model deployment name" : "Model ID"}</label><input id="model-${provider.id}" name="model" value="${escape(provider.model)}" placeholder="${provider.id === "azure" ? "your-deployment" : "model-id"}" maxlength="500"></div><div class="field"><label for="endpoint-${provider.id}">Inference endpoint</label><input id="endpoint-${provider.id}" name="endpoint" type="url" value="${escape(provider.endpoint)}" placeholder="https://…" maxlength="2000"><small>Full HTTPS URL, including the inference path.</small></div><div class="field"><label for="key-${provider.id}">API key</label><input id="key-${provider.id}" name="api_key" type="password" autocomplete="new-password" placeholder="${provider.has_key ? "Key stored · enter to replace" : "Enter your API key"}" maxlength="16000"><div class="key-actions"><span class="key-status">${provider.has_key ? "● A key is saved" : "○ No key configured"}</span><label class="clear-label"><input name="clear_key" type="checkbox">Clear saved key</label></div><small>Leave blank to keep your current key.</small></div><details class="price-block" ${provider.input_usd_per_million != null || provider.output_usd_per_million != null ? "open" : ""}><summary>Custom pricing · USD per 1M tokens</summary><div class="field-grid two"><div class="field"><label for="input-price-${provider.id}">Input rate</label><input id="input-price-${provider.id}" name="input_usd_per_million" type="number" min="0" step="any" value="${escape(provider.input_usd_per_million)}" placeholder="${provider.id === "openai" ? "0.10" : "0.042"}"></div><div class="field"><label for="output-price-${provider.id}">Output rate</label><input id="output-price-${provider.id}" name="output_usd_per_million" type="number" min="0" step="any" value="${escape(provider.output_usd_per_million)}" placeholder="0"></div></div><div class="field"><small>Leave both blank for default decision API pricing. Set both to override.</small></div></details></div></section>`).join("")}</div><div class="info-note"><span>↳</span><div>Changes update your local <strong>.env</strong> file. Stored keys are never sent back to the browser. A running benchmark keeps the configuration it started with.</div></div><div id="form-error" class="form-error" role="alert" hidden></div><div class="config-footer"><span class="save-note">Only selected providers need credentials for a live run.</span><button class="button primary" id="save-config" type="submit">Save configuration <span>↗</span></button></div></form>`;
  $("#config-form").addEventListener("input", () => { state.dirty = true; });
  $("#config-form").addEventListener("submit", async event => {
    event.preventDefault(); const button = $("#save-config"); if (button.disabled) return;
    const providers = $$(".config-card").map(card => {
      const value = name => $(`[name=${name}]`,card).value;
      return {id:card.dataset.provider,name:value("name"),model:value("model"),endpoint:value("endpoint"),api_key:value("api_key") || null,clear_key:$("[name=clear_key]",card).checked,input_usd_per_million:value("input_usd_per_million") || null,output_usd_per_million:value("output_usd_per_million") || null};
    });
    button.disabled = true; button.textContent = "Saving…";
    try { const result = await api("/config",{method:"PUT",body:JSON.stringify({providers})}); state.providers = result.providers; state.dirty = false; renderConfig(); toast("Configuration saved to .env."); }
    catch (error) { showError(error); button.disabled = false; button.textContent = "Save configuration ↗"; }
  });
}

function renderHistory() {
  $("#content").innerHTML = `<div class="page-heading"><div><div class="eyebrow">YOUR EXPERIMENTS, ALL IN ONE PLACE</div><h1>Run history</h1><p>Every run leaves a record. Revisit the results, compare the signal.</p></div><a href="#run" class="button primary">New benchmark <span>↗</span></a></div><div class="toolbar"><input class="search" id="history-search" type="search" placeholder="Search prompts or models…" aria-label="Search run history" value="${escape(state.search)}"><div class="filter-chips" id="history-filters">${["all","live","demo"].map(filter => `<button type="button" data-filter="${filter}" class="${state.historyFilter === filter ? "active" : ""}">${filter === "all" ? "All runs" : filter === "live" ? "Live" : "Demo"}</button>`).join("")}</div></div><div class="history-list" id="history-list"></div>`;
  $("#history-search").oninput = event => { state.search = event.target.value; renderHistoryList(); };
  $("#history-filters").onclick = event => { const button = event.target.closest("[data-filter]"); if (!button) return; state.historyFilter = button.dataset.filter; $$("#history-filters button").forEach(item => item.classList.toggle("active",item === button)); renderHistoryList(); };
  renderHistoryList();
}

function renderHistoryList() {
  if (!$("#history-list")) return;
  const search = state.search.toLowerCase();
  const runs = state.runs.filter(job => (state.historyFilter === "all" || (state.historyFilter === "demo") === !!job.settings?.demo) && `${job.prompt_name} ${job.providers.map(p => `${p.name} ${p.model}`).join(" ")}`.toLowerCase().includes(search));
  $("#history-list").innerHTML = runs.map(job => `<a class="panel history-card" href="${pathFor("history",job.id)}"><div class="recent-icon">${job.status === "completed" ? "✓" : job.status === "running" ? "◷" : "!"}</div><div class="history-details"><h2>${escape(job.prompt_name)}</h2><div class="history-meta"><span>${escape(date(job.started_at))}</span><span>·</span><span>${escape(job.providers.map(p => p.name).join(" / "))}</span>${job.settings?.demo ? badge("demo","DEMO") : ""}</div></div><div class="history-stat"><strong>${count(job.total)}</strong><small>REQUESTS</small></div><div class="history-stat">${badge(job.status)}<small>${job.status === "running" ? `${count(job.completed)} completed` : "SAVED LOCALLY"}</small></div><span class="history-arrow">↗</span></a>`).join("") || `<div class="empty-state"><div class="empty-icon">◷</div><h2>${state.runs.length ? "No matching runs" : "A clean slate."}</h2><p>${state.runs.length ? "Change the search or filter to see more results." : "Your benchmarks will be saved here, ordered from newest to oldest."}</p><a href="#run" class="button primary">Run your first benchmark ↗</a></div>`;
}

function partial(value, requests, total) { return `${count(value)}${value != null && requests != null && requests < total ? "*" : ""}`; }

function comparisonWinners(results) {
  const entries = Object.entries(results);
  const valid = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const workloads = entries.map(([, result]) => result.total_test_runs);
  const sameWorkload = workloads.length > 0 && workloads.every(total => valid(total) && total > 0 && total === workloads[0]);
  const completeUsage = (result, field) => sameWorkload && result.usage_requests?.[field] === result.total_test_runs ? result.usage?.[field] : null;
  const metrics = {
    correct: {higher:true, value:result => result.success_rate, max:100},
    requests: {higher:true, value:result => result.request_success_rate, max:100},
    avg: {value:result => result.latency_ms?.successful?.avg},
    p90: {value:result => result.latency_ms?.successful?.p90},
    input: {value:result => completeUsage(result, "input_tokens")},
    output: {value:result => completeUsage(result, "output_tokens")},
    cost: {value:result => sameWorkload && result.cost_complete === true ? result.estimated_cost_usd : null},
  };
  return Object.fromEntries(Object.entries(metrics).map(([key, metric]) => {
    const values = entries.map(([id, result]) => [id, metric.value(result)]).filter(([, value]) => valid(value) && (metric.max == null || value <= metric.max));
    if (!values.length) return [key, new Set()];
    const best = values.reduce((winner, [, value]) => metric.higher ? Math.max(winner, value) : Math.min(winner, value), values[0][1]);
    return [key, new Set(values.filter(([, value]) => value === best).map(([id]) => id))];
  }));
}

function renderComparisonTable(job, report) {
  const results = report.provider_results || {};
  const modelCount = Object.keys(results).length;
  const winners = comparisonWinners(results);
  const crown = label => `<span class="winner-mark" role="img" aria-label="${escape(label)}" title="${escape(label)}"><svg class="winner-crown" viewBox="0 0 24 24" aria-hidden="true"><path d="m3 6 4 4 5-7 5 7 4-4-2 12H5L3 6Z"/><path d="M6 21h12"/></svg></span>`;
  const cell = (id, metric, content, label) => {
    const winning = winners[metric].has(id);
    const crowned = winning && winners[metric].size < modelCount;
    return `<td${winning ? ' class="winner-cell"' : ""}><div class="comparison-value">${content}${crowned ? crown(`${label}${winners[metric].size > 1 ? " (tied)" : ""}`) : ""}</div></td>`;
  };
  const rows = Object.entries(results).map(([id, result]) => {
    const provider = job.providers.find(item => item.id === id);
    const timings = result.latency_ms?.successful || {};
    const accuracy = Math.min(100, Math.max(0, Number(result.success_rate) || 0));
    const timing = value => value == null ? "—" : Number(value).toFixed(1);
    return `<tr><td class="model-cell"><div class="comparison-model">${providerLogo(id)}<div><strong>${escape(provider?.name || id)}</strong><small>${escape(result.model)}</small></div></div></td>
      ${cell(id, "correct", `<div class="accuracy-value">${percent(result.success_rate)}<span class="accuracy-track"><span style="width:${accuracy}%"></span></span></div>`, "Highest correct decision rate")}
      ${cell(id, "requests", percent(result.request_success_rate), "Highest request success rate")}
      ${cell(id, "avg", timing(timings.avg), "Lowest average latency")}
      ${cell(id, "p90", timing(timings.p90), "Lowest P90 latency")}
      ${cell(id, "input", partial(result.usage?.input_tokens, result.usage_requests?.input_tokens, result.total_test_runs), "Lowest complete input token total")}
      ${cell(id, "output", partial(result.usage?.output_tokens, result.usage_requests?.output_tokens, result.total_test_runs), "Lowest complete output token total")}
      ${cell(id, "cost", `${money(result.estimated_cost_usd)}${result.estimated_cost_usd != null && !result.cost_complete ? "*" : ""}`, "Lowest complete cost")}</tr>`;
  }).join("");
  return `<section class="panel model-comparison"><div class="panel-head"><div><h2>The model comparison</h2><p>Correctness includes failed requests. Timing reflects valid responses.</p></div><span class="comparison-legend">${crown("Best reported value")}Best in column</span></div><div class="table-scroll"><table><thead><tr><th scope="col">Provider / model</th><th scope="col">Correct ↑</th><th scope="col">Requests OK ↑</th><th scope="col">Avg ms ↓</th><th scope="col">P90 ms ↓</th><th scope="col">Input tokens ↓</th><th scope="col">Output tokens ↓</th><th scope="col">Cost USD ↓</th></tr></thead><tbody>${rows || '<tr><td colspan="8">No model results available.</td></tr>'}</tbody></table></div><p class="comparison-note">Best reported values are highlighted. Tied leaders share crowns; when every model ties, only the highlight remains. ↑ Higher is better · ↓ Lower is better. Token and cost totals are ranked only with complete data for equal workloads.</p></section>`;
}

async function renderReport(id) {
  $("#content").innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Opening this benchmark…</p></div>';
  try {
    const data = await api(`/runs/${encodeURIComponent(id)}`);
    if (location.hash !== pathFor("history",id)) return;
    state.report = data; showReport(data);
  } catch (error) { if (location.hash === pathFor("history",id)) renderMissing(error.message,"#history"); }
}

function showReport({job,report}) {
  const heading = `<a class="back-link" href="#history">← Back to run history</a><div class="page-heading"><div><div class="eyebrow">BENCHMARK RESULTS</div><h1>${escape(job.prompt_name)}</h1><p>${escape(date(job.started_at))} · ${job.providers.length} models · ${badge(job.status)} ${job.settings?.demo ? badge("demo","DEMO") : ""}</p></div><div class="button-row">${report ? `<a class="button secondary" href="/api/runs/${encodeURIComponent(job.id)}/download" download>Download JSON ↓</a>` : ""}<a class="button primary" href="#run" data-rerun="${escape(job.id)}">Run again ↗</a></div></div>`;
  if (!report) {
    $("#content").innerHTML = `${heading}<section class="panel"><div class="panel-body"><div class="eyebrow">${job.status === "running" ? "EXPERIMENT IN PROGRESS" : "RUN STATUS"}</div><h2 style="font-weight:500;font-size:22px;letter-spacing:-.6px">${job.status === "running" ? "The results are on their way." : job.status === "interrupted" ? "This run was interrupted." : "This run could not finish."}</h2><p class="empty-small">${job.status === "running" ? "You can explore the library or update your configuration. The progress banner will stay with you." : escape(job.error || "The server stopped before the benchmark could be saved.")}</p><div class="divider"></div><div class="progress-label" style="color:var(--muted)"><span>${count(job.completed)} / ${count(job.total)} requests</span><span>${Math.floor(100 * job.completed / Math.max(1,job.total))}%</span></div><div class="progress-track" style="background:#e2eef6"><span style="width:${100 * job.completed / Math.max(1,job.total)}%;background:var(--blue)"></span></div></div></section>`;
    return;
  }
  const latency = report.latency_ms?.successful || {};
  const configuration = report.configuration || {};
  const duration = (new Date(report.finished_at) - new Date(report.started_at)) / 1000;
  $("#content").innerHTML = `${heading}<div class="report-settings"><span class="pill">${report.prompt?.dataset?.length || 0} dataset rows</span><span class="pill">${configuration.repetitions || 1} repetitions / row</span><span class="pill">${configuration.concurrency || 1} concurrent</span><span class="pill">${configuration.timeout_seconds || 30}s timeout</span><span class="pill">${Number.isFinite(duration) ? duration.toFixed(1) : "—"}s elapsed</span></div><div class="report-stats"><div class="panel report-stat"><div class="stat-label">◈ CORRECT DECISIONS</div><strong>${percent(report.success_rate)}</strong><small>${count(report.correct_predictions)} correct / ${count(report.total_test_runs)} attempts</small></div><div class="panel report-stat"><div class="stat-label">◷ AVERAGE LATENCY</div><strong>${latency.avg == null ? "—" : Number(latency.avg).toFixed(1)}<span style="font-size:12px"> ms</span></strong><small>Across valid responses</small></div><div class="panel report-stat"><div class="stat-label">≋ INPUT TOKENS</div><strong>${partial(report.usage?.input_tokens,report.usage_requests?.input_tokens,report.total_test_runs)}</strong><small>Provider-reported usage</small></div><div class="panel report-stat"><div class="stat-label">↗ ESTIMATED COST</div><strong style="font-size:21px">${money(report.estimated_cost_usd)}${report.estimated_cost_usd != null && !report.cost_complete ? "*" : ""}</strong><small>USD · applied decision API pricing</small></div></div>
  ${renderComparisonTable(job, report)}<div class="info-note"><span>↳</span><div>${job.settings?.demo ? "Demo responses, timings, usage, and costs are synthetic. " : ""}* marks partial usage or cost; — means unavailable. Old reports may lack token and cost totals. Estimates use the rate snapshots saved with this run.</div></div>
  <section class="panel report-section"><div class="panel-head"><div><h2>Every decision, explained by the data</h2><p>Browse expected answers, predictions, and failures.</p></div><div class="request-filter"><select id="request-provider" aria-label="Filter requests by provider"><option value="">All models</option>${job.providers.map(p => `<option value="${escape(p.id)}">${escape(p.name)}</option>`).join("")}</select><select id="request-outcome" aria-label="Filter requests by outcome"><option value="">All outcomes</option><option value="correct">Correct</option><option value="wrong">Incorrect</option><option value="error">Request errors</option></select></div></div><div class="table-scroll"><table><thead><tr><th>Query</th><th>Model</th><th>Expected → predicted</th><th>Latency ms</th><th>Input tokens</th><th>Cost USD</th></tr></thead><tbody id="request-rows"></tbody></table></div><div class="panel-body" style="padding-top:12px;padding-bottom:12px"><span id="request-pagination" class="save-note"></span> <button type="button" class="button ghost small" id="more-requests" hidden>Show 100 more ↓</button></div></section>
  <section class="panel report-section"><div class="panel-head"><h2>Prompt snapshot</h2><span class="step">AS TESTED</span></div><div class="panel-body"><p class="report-prompt">${escape(report.prompt?.prompt)}</p><div class="preview-pills" style="margin:18px 0 0">${(report.prompt?.choices || []).map(choice => `<span class="pill">${escape(choice.value)}</span>`).join("")}</div></div></section><details class="json-details"><summary>View complete report JSON</summary><pre id="raw-report"></pre></details>`;
  let limit = 100;
  function showRequests() {
    const provider = $("#request-provider").value, outcome = $("#request-outcome").value;
    const requests = (report.per_request || []).filter(row => (!provider || row.provider === provider) && (!outcome || (outcome === "correct" ? row.correct : outcome === "wrong" ? !row.correct && !row.error : !!row.error)));
    $("#request-rows").innerHTML = requests.slice(0,limit).map(row => `<tr><td class="request-cell">${escape(row.query)}</td><td>${escape(job.providers.find(p => p.id === row.provider)?.name || row.provider)}<br><small>Row ${Number(row.dataset_index)+1} · rep ${escape(row.repetition)}</small></td><td><span class="${row.correct ? "result-check" : "result-cross"}">${row.correct ? "✓" : "×"}</span> ${escape(row.expected)} → ${escape(row.predicted || row.error || "—")}</td><td>${row.latency_ms == null ? "—" : Number(row.latency_ms).toFixed(1)}</td><td>${count(row.usage?.input_tokens ?? row.response?.usage?.input_tokens)}</td><td>${money(row.estimated_cost_usd)}</td></tr>`).join("") || '<tr><td colspan="6">No requests match this filter.</td></tr>';
    $("#request-pagination").textContent = `Showing ${Math.min(limit,requests.length)} of ${count(requests.length)} matching requests`;
    $("#more-requests").hidden = limit >= requests.length;
  }
  $("#request-provider").onchange = $("#request-outcome").onchange = () => { limit = 100; showRequests(); };
  $("#more-requests").onclick = () => { limit += 100; showRequests(); };
  $(".json-details").addEventListener("toggle", event => {
    if (event.target.open && !$("#raw-report").textContent) $("#raw-report").textContent = JSON.stringify(report,null,2);
  });
  showRequests();
}

function renderMissing(message, back) { $("#content").innerHTML = `<div class="empty-state"><div class="empty-icon">!</div><h2>Something needs attention.</h2><p>${escape(message)}</p><a href="${escape(back)}" class="button secondary">Go back ←</a></div>`; }

function updateCounts() { $("#prompt-count").textContent = state.prompts.length; $("#run-count").textContent = state.runs.length; }

function renderBanner() {
  const target = $("#run-banner"), job = state.active || state.banner;
  target.hidden = !job; document.body.classList.toggle("has-run",!!job);
  if (!job) return;
  const running = job.status === "running";
  const completed = job.status === "completed";
  const progress = Math.min(100,Math.floor(100 * job.completed / Math.max(1,job.total)));
  target.innerHTML = `<div class="banner-icon">${running ? '<span class="pulse-dot"></span>' : completed ? "✓" : "!"}</div><div class="banner-copy"><strong>${running ? "Benchmark running" : completed ? "Benchmark complete" : job.status === "interrupted" ? "Benchmark interrupted" : "Benchmark failed"} · ${escape(job.prompt_name)}</strong><small>${job.providers.length} models · ${job.settings?.demo ? "Offline demo" : "Live requests"} · Navigate freely while your test runs</small></div><div class="banner-progress"><div class="progress-label"><span>${count(job.completed)} / ${count(job.total)}</span><span>${progress}%</span></div><div class="progress-track"><span style="width:${progress}%"></span></div></div><a class="banner-button" href="${pathFor("history",job.id)}">${running ? "View run" : "View results"} ↗</a>${running ? "" : '<button class="banner-dismiss" aria-label="Dismiss run banner">×</button>'}`;
  if ($(".banner-dismiss",target)) $(".banner-dismiss",target).onclick = () => { state.banner = null; state.tracked = null; sessionStorage.removeItem("decision-tracked"); renderBanner(); };
}

function notifyCompletion(job) {
  if (state.notified.has(job.id)) return;
  state.notified.add(job.id);
  const success = job.status === "completed";
  const message = success ? `${job.prompt_name}: benchmark complete.` : `${job.prompt_name}: ${job.status}. ${job.error || ""}`;
  toast(message,!success,pathFor("history",job.id));
  if ("Notification" in window && Notification.permission === "granted") {
    try { const notification = new Notification(success ? "Decision Lab — results ready" : "Decision Lab — run stopped",{body:message,tag:job.id}); notification.onclick = () => { window.focus(); location.hash = pathFor("history",job.id); notification.close(); }; } catch { /* in-app notification remains available */ }
  }
}

let pollFailed = false;
async function poll() {
  try {
    const data = await api("/runs"); state.runs = data.runs; state.active = data.active;
    const tracked = state.runs.find(job => job.id === state.tracked);
    if (tracked) { state.banner = tracked; if (tracked.status !== "running") notifyCompletion(tracked); }
    if (state.active && !state.tracked) { state.tracked = state.active.id; sessionStorage.setItem("decision-tracked",state.tracked); }
    renderBanner(); updateCounts(); renderRecent(); renderHistoryList(); updateRunPreview();
    if (state.report?.job.status === "running") {
      const current = state.runs.find(job => job.id === state.report.job.id);
      if (current?.status !== "running" && location.hash === pathFor("history",state.report.job.id)) await renderReport(state.report.job.id);
    }
    pollFailed = false;
  } catch {
    if (!pollFailed) toast("Connection lost. Retrying the local server…",true);
    pollFailed = true;
  } finally { setTimeout(poll,1200); }
}

async function route() {
  const hash = location.hash || "#run";
  if (hash === state.route) return;
  if (state.dirty && state.route) {
    const dialog = $("#confirm-dialog"); dialog.returnValue = "stay"; dialog.showModal();
    const decision = await new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue),{once:true}));
    if (decision !== "leave") { history.replaceState(null,"",state.route); return; }
  }
  state.dirty = false; state.route = hash; state.report = null;
  const [section, encodedId] = hash.slice(1).split("/");
  let id;
  try { id = encodedId ? decodeURIComponent(encodedId) : null; } catch { id = null; }
  const labels = {run:"Run benchmark",prompts:"Prompt library",history:"Run history",config:"Configuration"};
  $("#page-label").textContent = labels[section] || "Run benchmark";
  $$('[data-nav]').forEach(item => {
    const active = item.dataset.nav === section;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  if (section === "prompts") id ? renderEditor(id) : renderLibrary();
  else if (section === "config") renderConfig();
  else if (section === "history") id ? await renderReport(id) : renderHistory();
  else renderRun();
  if (location.hash === hash || (!location.hash && hash === "#run")) $("#content").focus({preventScroll:true});
  window.scrollTo({top:0,behavior:"instant"});
}

document.addEventListener("click", event => {
  if (event.target.closest(".skip-link")) {
    event.preventDefault();
    $("#content").focus();
    return;
  }
  const link = event.target.closest("[data-rerun]");
  if (!link) return;
  const job = state.runs.find(item => item.id === link.dataset.rerun);
  const prompt = state.prompts.find(item => item.name === job?.prompt_name);
  if (!job) return;
  if (!prompt) { toast("The original prompt is no longer in the library. Select another prompt before running.",true); return; }
  state.draft = {prompt_id:prompt.id,providers:job.providers.map(provider => provider.id).filter(id => state.providers.some(p => p.id === id)),...job.settings};
  sessionStorage.setItem("decision-draft",JSON.stringify(state.draft));
});
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
$("#notify-button").onclick = async () => {
  if (!("Notification" in window)) { toast("Browser notifications are unavailable. Completion notices will still appear here."); return; }
  const permission = await Notification.requestPermission();
  toast(permission === "granted" ? "Browser notifications enabled. We’ll let you know when a run finishes." : "Completion notices will appear inside the app.");
};

async function boot() {
  try {
    const data = await api("/bootstrap");
    state.providers = data.providers; state.prompts = data.prompts; state.runs = data.runs; state.active = data.active;
    state.demoOnly = data.demo;
    if (data.demo || !Object.hasOwn(savedDraft,"demo")) state.draft.demo = data.demo;
    if (state.active) { state.banner = state.active; state.tracked = state.active.id; sessionStorage.setItem("decision-tracked",state.tracked); }
    updateCounts(); renderBanner(); await route(); poll();
  } catch (error) { renderMissing(error.message,"#run"); toast("The workspace could not load. Check the server output and your prompt files.",true); }
}
boot();
