# Decision Lab visual design

The dashboard uses a blue and white palette with azure (`#0070ad`) for primary
controls, pale blue previews, a white horizontal header, and a cool neutral
workspace. It retains Decision Lab's own identity and contains no Capgemini logo.

## Generated benchmark hero

- Asset: `src/decision_tests/static/decision-api-office.webp`
- Tool: built-in `image_gen`, new image, opaque background
- Original dimensions: 1536 × 1024
- Delivery: WebP, quality 85, approximately 91 KB, served locally
- Placement: right side of the run benchmark hero on desktop; below its copy on mobile

Final generation prompt:

> Use case: ads-marketing. Asset type: hero image for the upper-right half of a clean modern decision API benchmark dashboard. Generate a polished landscape 3:2 editorial photographic composite, designed to be cropped into a 550x300 hero area. Scene: two diverse professionals in a bright glass-walled contemporary office collaborating at a laptop, natural candid expressions, elegant white desks and soft daylight. In the foreground integrate a tasteful semi-transparent blue technology overlay: a central rounded square decision-processing node connected by thin cyan lines to three smaller output nodes, subtle structured data and tiny chart elements, conveying one input routed into choices and model comparisons. Keep this technology overlay understated and visually precise rather than cluttered sci-fi holograms. Palette: white, cool pale gray, rich corporate azure blue #0070ad and restrained cyan accents; no green. Composition: people in middle/right, readable central decision branching graphic, soft blue fade at far left to blend with an adjacent blue hero panel. Premium enterprise aesthetic, realistic people, crisp tasteful lighting, optimistic calm mood. No text, no lettering, no logos, no brand marks, no watermark. Make an actual photographic visual, not a screenshot or webpage mockup.

## Validation and routing

Implementation and integration stayed with the primary agent. Bounded read-only
exploration and independent review were delegated, requesting `gpt-6-luna` at
`max` reasoning, to isolate UI contract and review work. The tool did not expose
runtime model metadata. Review refinements included mobile text readability,
wrapping action groups, and visible keyboard focus after navigation.

- `uv run pytest tests/test_frontend.py tests/test_web.py -q`: 15 passed
- `uv run ruff check src/decision_tests/web.py tests/test_web.py`: passed
- JavaScript syntax and existing Node behavior checks: passed
- Static shell IDs, navigation targets, image reference, CSS structure, and focus styling: passed
- Browser visual verification: unavailable; Chrome exits with `setsockopt: Operation not permitted` under the environment's socket restrictions

The review also identified existing queued-run notification and live-region
polling behavior; those are unchanged by this visual redesign.

## Provider logos

The benchmark provider cards use 56px logos and configuration headers use 48px
logos. Both display the artwork directly, without the former avatar border,
background, or box; `object-fit: contain` preserves the original proportions.
Mobile provider cards keep the full 56px size next to readable model details.

Published assets and brand references:

- TypeSafe AI: [company repository logo](https://github.com/typesafe-ai/typesafe-ai.github.io/blob/main/logo.svg), also referenced by that repository's TypeSafe website as its favicon. Its raw SVG is used directly. Current Framer-site artwork could not be inspected for comparison.
- OpenAI: black Blossom from [OpenAI's published brand asset](https://cdn.openai.com/brand/OpenAI-black-monoblossom.svg); [brand reference](https://openai.com/brand/).
- Azure: transparent vector [Azure logo asset](https://github.com/gilbarbara/logos/blob/main/logos/microsoft-azure.svg) distributed by the Logos collection; [Microsoft's Azure icon reference](https://azure.microsoft.com/en-us/blog/a-fluent-new-look-for-the-azure-icon/).

The logos load from external published URLs with `referrerpolicy="no-referrer"`.
Unlike the locally served generated hero, they require an internet connection.
Shell downloads failed due to the environment's DNS restrictions, so no local
logo copies or browser rendering could be verified. Provider names and controls
remain available without these images. Logos belong to their respective owners.

This bounded follow-up kept implementation with the primary agent and delegated
TypeSafe source research and a focused read-only review, requesting `gpt-6-luna`
with `medium` reasoning. JavaScript syntax and existing frontend behavior checks,
CSS structure checks, and all 15 focused frontend/server tests passed.

## Comparison winners

The model comparison table now includes an unboxed 30px provider logo beside
each model and separate columns for minimum latency, average latency, P90 latency,
and input tokens. Output tokens are omitted from the comparison display. Each
metric highlights its best reported values only when another comparable value
is worse. A sole leader gets a small gold SVG crown; tied leaders get highlights
without crowns. Equal columns and single-model comparisons get neither winner
highlights nor crowns. Except for correctness, winner backgrounds are pale blue.
Numbers use a larger, centered font. The crown is positioned above the number
outside normal layout flow, keeping crowned and uncrowned values aligned.

Correct-decision and request-success rates rank highest first; latency, token
usage, and estimated cost rank lowest first. Missing, negative, nonfinite, and
out-of-range percentage values are excluded. Token and cost totals are ranked
only when providers have equal positive request counts, usage coverage is
complete for the specific token metric, and costs are marked complete. No
aggregate overall winner is inferred from these independent metrics.

Correctness uses a larger percentage with no progress bar. The whole cell uses
a score-dependent shade: red at 50% or below, orange above 50% and below 70%,
yellow from 70% to below 90%, and green from 90% to 100%. Orange, yellow, and
green become stronger toward the top of their band; red becomes stronger as
accuracy falls. The correctness shade takes precedence over winner highlighting,
while crowns still identify leaders. Missing or invalid accuracy uses a neutral
background. Minimum latency uses valid responses, like average and P90.

Input tokens remain the sum of each API's reported request usage. Providers receive
the same instructions, choices, and dataset queries in their API-specific request
formats, and may tokenize or account for them differently. The table explains
that these are provider-reported totals. Output usage remains available in raw
reports for cost calculations.

Implementation and test updates remained with the primary agent. This follow-up
used bounded read-only token-accounting investigation and review, requesting
`gpt-6-luna` at `medium` reasoning to isolate independent work. Runtime model
metadata was not exposed. Review found no functional defects. All 15 focused
frontend/server tests passed, along with JavaScript syntax and diff whitespace
checks. Node checks cover independent minimum/average/P90 winners, ties, zero
values, partial data, unequal workloads, safe HTML rendering, accessible crowns,
provider logos, correctness band boundaries, shade variation, and invalid scores.
Browser rendering was not verified; checks validate generated markup and source
selectors.

This layout and tie-rule refinement stayed solo as a small local change.
