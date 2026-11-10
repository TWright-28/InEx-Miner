# BUG CLASSIFICATION GUIDE

This guide classifies GitHub issues into one of four categories:
**Intrinsic, Extrinsic, Not a Bug, or Unknown.**

You must reason carefully, prioritize maintainer signals, and classify based on the strongest evidence.

---

# DEFINITIONS (From a maintainer's perspective)

## INTRINSIC - The root cause is inside this repository.

A maintainer would fix the issue by modifying **production code in this repo**.

Includes:

- Logic/validation/exception/flow errors in our source code
- Missing null/undefined checks
- Incorrect async/promise usage
- Our SQL/migrations/schema/scripts wrong or incomplete
- Regressions caused by our own commits in this repo
- Misusing a dependency/API relative to its current contract
- Incorrect or incomplete package.json fields (exports, peer deps, entry points)
- Configurations or code we ship that cause dependency/toolchain issues
- Transformation/formatting/transpilation bugs caused by our logic/config
- Platform/OS behavior **within** supported environments where our code fails

---

## EXTRINSIC - The root cause is outside this repository.

The repo must adapt to an **external change** (dependency, platform, runtime).

Includes:

- Dependency / Node / browser / toolchain upgrades causing breaking changes
- npm/yarn/pnpm resolution changes
- Platform/OS differences (within support) caused by external runtime behavior
- Upstream API deprecations/removals while we used them correctly
- Node/V8/ESM behavior changes
- Build toolchain updates (Webpack/Babel/TS/Rollup/esbuild/Vite)
- Parser/formatter/linter behavior changes from upstream
- Database engine rule changes (Postgres/MySQL)
- Issues appearing with **no code changes** in this repo, except version bumps that pulled in external breaking changes

---

## NOT A BUG - Not a defect in this repo's production code.

Includes:

- All testing related things
- All documentation issues (missing/incorrect/unclear)
- All usage questions ("How do I…?", "Can you explain…?")
- Feature requests, enhancement requests
- User misuse, invalid environment setup, incorrect configuration
- Missing peer dependencies the user must install themselves
- Unsupported platforms or environments outside documented support
- Failures in the user's application code or build tools
- Consumer bundler/minifier/parser limitations
- Workspace/monorepo config issues in user's setup
- Duplicates or administrative/tracking issues
- User-pinned or nonstandard dependency versions outside supported ranges
- Housekeeping closures without production code fixes

---

## UNKNOWN - Insufficient or unintelligible information.

Includes:

- Non-English content with no interpretable technical info
- Missing versions, missing reproduction steps, missing error logs
- Very vague, contradictory, or minimal descriptions
- "Cannot reproduce", "need repro", "stale" **with no fix PR**
- Insufficient data to infer Intrinsic vs Extrinsic vs Not a Bug

---

# CORE DECISION PRINCIPLES

_(Flexible; not strict rules. Use these concepts to reason.)_

- **Maintainer comments are the strongest signal**  
  Owners/Members/Collaborators often state the real cause directly.

- **Closing PR/commit indicates a real bug**  
  If the PR touches production code -> Almost never Unknown or Not a Bug.  
  Classify Intrinsic vs Extrinsic based on what the PR fixed.

- **Labels are weak hints**  
  Labels may be wrong. Always check content first.

- **Misconfigurations and questions -> usually Not a Bug**  
  If the user setup violates documented requirements.

- **External change + correct usage -> Extrinsic**
- **Incorrect usage of external APIs -> Intrinsic**  
  Even if the failure happens inside node_modules.

- **Platform rules:**
  - Within support -> Intrinsic or Extrinsic
  - Outside support -> Not a Bug

- **Peer dependency logic:**
  - Missing peer dep the user must install -> Not a Bug
  - Wrong peer dep declaration -> Intrinsic

- **Unknown only when inference is impossible**

---

# OPERATIONAL RULES

These rules prevent common classification errors.

---

## 1. INTRINSIC EVIDENCE GATE

Before choosing Intrinsic, there must be clear evidence that the defect originates inside this repository’s own logic.
A PR alone is not enough unless the maintainer explicitly confirms the bug is internal.

Intrinsic should only be selected when one or more of the following hold:
A maintainer (OWNER / MEMBER / COLLABORATOR) explicitly states the problem is caused by this repository’s code.
A stack trace or reproduction clearly shows failure inside this repo’s own source code (not upstream, not user code).
The fix PR explicitly corrects an internal logic/behavior error, not:
-compatibility updates
-refactors
-config cleanups
-feature additions
-upstream workaround patches
-Valid supported inputs produce incorrect behavior due to our logic, not due to external changes or misuse.
If the evidence does NOT clearly show the bug originates in this repo’s logic -> Do NOT choose Intrinsic.

---

## 2. TEMPORAL SIGNALS FOR EXTRINSIC

These phrases strongly indicate Extrinsic classification:

- "after upgrading [dependency/Node/tool]"
- "after npm update" / "after updating dependencies"
- "since version X" / "started when upgrading to…"
- "works on [X], fails on [Y]"
- "breaking change in [dependency]"
- "worked yesterday, broken today" (with no code changes)

**Extrinsic bugs are more common when these appear**

---

## 3. CONTENT > LABELS (Absolute Rules)

The following are **Not a Bug** regardless of labels:

- Test failures, flaky tests, CI configuration issues
- Usage questions ("How do I...?")
- Documentation issues, requests, or improvements
- Feature requests or enhancement suggestions
- License-related issues
- Administrative/duplicate/tracking issues

**If content contradicts labels -> Trust the content, not the label.**

---

## 4. CONTRACT RULE

This resolves many borderline cases:

- If **we** misuse or violate the current/documented API -> **Intrinsic**
- If **the dependency/platform changed** the API/behavior -> **Extrinsic**

Example: We pass `undefined` to a library that requires a string -> Intrinsic (our fault)  
Example: Library removes an endpoint we were using correctly -> Extrinsic (their change)

---

## 5. DEPENDENCY BUMP CLARIFICATION

A version bump commit does NOT automatically make a bug Intrinsic.

**Scenario:** The only change in this repo was:  
`chore: bump webpack 4.0 -> 5.0`  
and the breakage comes from webpack 5's breaking changes.

**Classification:** This is **Extrinsic**, not Intrinsic.  
The root cause is the external breaking change, even though there's a commit in this repo.

---

## 6. TEST AMBIGUITY HANDLING

Tests and CI require careful classification:

- **Clearly test-only issue** (wrong mock, flaky assertion, CI config) -> **Not a Bug**
- **Test exposes a real production bug** -> Classify the production bug (Intrinsic/Extrinsic)
- **Unclear whether test-only or production bug** -> **Unknown**

---

## 7. SIGNAL PRIORITY ORDER

When signals conflict, prioritize in this order:

1. **Maintainer comments** (highest authority)
2. **Closing PR/commit** (strong evidence of real bug)
3. **Actual content** (title, body, repro, stack trace)
4. **Labels** (weakest signal)

---

### Upstream Library vs Our Responsibility

Always classify from the perspective of **the repository the issue is filed in**.

- If the issue describes a real defect in this repository
  that only appears because an external dependency / runtime changed,
  and we would reasonably need to change our code or configuration to fix it,
  then the issue is **Extrinsic**.

- If the issue describes a defect or limitation in an external library,
  or a misuse of that library, and there is **no bug or required change in this repository**,
  then the issue is **Not a Bug** for this repo, even if a real bug exists upstream.

**Signals for "Not a Bug (upstream/misuse)" include:**

- Maintainer or reporter links to an upstream issue/PR and concludes
  that the fix is entirely in that other project.
- Maintainer responds along the lines of "this is not a bug in this repo",
  "this is an upstream issue", or "this is misuse / unsupported usage".
- The repository does not change its own production code in response
  (no fix PR here, only an upstream fix or user configuration change).

In these cases, do **not** classify as Extrinsic.
From this repository's perspective, the correct label is **Not a Bug**.

# EXAMPLES WITH REASONING

These examples demonstrate the classification process.

---

## Example 1 - Intrinsic (Logic Bug)

**Bug Project:** data-validator  
**Bug Title:** "Validation allows negative values when min is 0"  
**Bug Description:** Setting min: 0 should reject negative numbers but -5 passes validation. Comparison logic error in validator.  
**Labels:** None  
**Maintainer Comments:** None explicit  
**Closing PR:** Yes - fixed comparison logic in validation module

**Reasoning:**
**Definition Check:** Logic error in our source code -> Intrinsic candidate
**Maintainer Signal:** No explicit comment, but closing PR provides evidence
**PR/Commit Evidence:** PR modifies internal validation logic in production code. The fix corrects comparison operators.
**Intrinsic Evidence Gate:** PASSES - Closing PR modifies this repo's production code to fix incorrect behavior
**Temporal Signals:** None present (no "after upgrade" or version change mentioned)
**Contract Rule:** Not applicable - this is internal logic, not API usage
**Information Completeness:** Sufficient - clear description, reproduction case, and fix
**Decision:** Clear internal logic bug. Comparison operators in our validator were incorrect. PR fixes production code in this repo.
**Confidence:** High - clear internal logic bug with production code fix

**Probability Distribution:**

- Intrinsic: 0.90
- Extrinsic: 0.02
- Not a Bug: 0.05
- Unknown: 0.03

**Final Answer:** Intrinsic

---

## Example 2 - Extrinsic (Dependency Breaking Change)

**Bug Project:** webpack-plugin  
**Bug Title:** "Build fails after webpack 4 ->5 upgrade"  
**Bug Description:** After upgrading webpack to 5.0, builds fail with "unknown config property". Same config worked perfectly in webpack 4. No changes to our code.  
**Labels:** bug, dependencies  
**Maintainer Comments:** "Webpack 5 changed the config schema. We need to update our plugin config."  
**Closing PR:** Yes - updated plugin configuration for webpack 5 compatibility

**Reasoning:**
**Definition Check:** External tool breaking change -> Extrinsic candidate
**Maintainer Signal:** Maintainer explicitly identifies external cause ("Webpack 5 changed...")
**PR/Commit Evidence:** PR only updates configuration to adapt to webpack 5's new schema. No logic bugs fixed.
**Temporal Signals:** STRONG - "after webpack upgrade" - clear temporal signal for Extrinsic
**Contract Rule:** Our config was valid for webpack 4. Webpack changed its requirements. We were NOT misusing the API.
**Dependency Bump Rule:** The repo likely has a commit bumping webpack version, but root cause is webpack's breaking change -> Still Extrinsic
**Decision:** Clear external breaking change. Webpack 5 removed/changed config options. Our code must adapt.
**Confidence:** High - explicit temporal signal, maintainer confirmation, external tool breaking change

**Final Answer:** Extrinsic

---

## Example 3 - Not a Bug (Question Mislabeled as Bug)

**Bug Project:** api-client  
**Bug Title:** "How do I use this with async/await?"  
**Bug Description:** I'm trying to use this library with async/await patterns but I'm not sure about the correct approach. Can you provide examples? I've tried various approaches but getting confused about promise handling.  
**Labels:** bug (mislabeled)  
**Maintainer Comments:** "This is a usage question, not a bug. Please see our documentation on async patterns at [link]. For further help, try Stack Overflow or our Discord."  
**Closing PR:** None

**Reasoning:**
**Definition Check:** Usage question despite "bug" label -> Not a Bug candidate
**Maintainer Signal:** STRONG - Maintainer explicitly says "This is a usage question, not a bug"
**Content > Labels Rule:** Content is clearly "How do I...?" question. Label says "bug" but content takes precedence.
**PR/Commit Evidence:** No closing PR. No production code changes needed.
**Information Completeness:** Complete information - this is not a defect, just a support request
**Decision:** Pure usage question. User is seeking help, not reporting a defect. Maintainer confirms. Label is misleading.
**Confidence:** High - clear support question confirmed by maintainer, despite misleading label

**Final Answer:** Not a Bug

---

## Example 4 - Not a Bug (Test Infrastructure)

**Bug Project:** react-component  
**Bug Title:** "Jest tests timeout on CI"  
**Bug Description:** Tests pass locally with no issues but consistently timeout on GitHub Actions CI. The timing-sensitive assertions are failing in the CI environment. Need to adjust timeouts for slower CI machines.  
**Labels:** bug (mislabeled)  
**Maintainer Comments:** "This is a test environment issue, not a production bug. I'll increase the timeouts."  
**Closing PR:** Yes - increased test timeouts and adjusted CI-specific test configuration

**Reasoning:**
**Definition Check:** Test infrastructure issue -> Not a Bug candidate
**Content > Labels Rule:** Content describes test timeouts and CI configuration, despite "bug" label
**Maintainer Signal:** Maintainer confirms "test environment issue, not a production bug"
**PR/Commit Evidence:** PR only modifies test configuration and timeouts. Zero production code changes.
**Test Ambiguity Handling:** This is CLEARLY test-only. No production bug exposed. Tests are too strict for CI.
**Decision:** Test infrastructure issue. No defect in production code. Only test configuration needs adjustment.
**Confidence:** High - test infrastructure only, no production code changes, maintainer confirms

**Final Answer:** Not a Bug

---

## Example 5 - Extrinsic (Runtime Version Change)

**Bug Project:** build-tool  
**Bug Title:** "Works on Node 16, fails on Node 18 with ERR_REQUIRE_ESM"  
**Bug Description:** Our builds work perfectly on Node 16 LTS. After upgrading to Node 18 LTS, we're getting module resolution errors: "ERR_REQUIRE_ESM: require() of ES Module not supported". No code changes on our end. Just upgraded Node version.  
**Labels:** bug  
**Maintainer Comments:** "Node 18 has stricter ESM handling. We need to update our package.json module structure."  
**Closing PR:** Yes - updated package.json with type: "module" and proper ESM exports for Node 18

**Reasoning:**
**Definition Check:** Runtime environment change -> Extrinsic candidate
**Maintainer Signal:** Maintainer identifies external cause ("Node 18 has stricter ESM handling")
**PR/Commit Evidence:** PR updates module configuration to work with Node 18's requirements. Adapting to external change.
**Temporal Signals:** STRONG - "Works on X, fails on Y" pattern. "After upgrading to Node 18". Clear external version change.
**Platform Support:** Both Node 16 and 18 are within typical LTS support matrix. The runtime changed its rules.
**Contract Rule:** We weren't violating Node 16's ESM rules. Node 18 introduced stricter requirements.
**Decision:** External runtime version changed behavior. Our code must adapt to new Node 18 ESM requirements.
**Confidence:** High - clear runtime version change with strong temporal signals
**Final Answer:** Extrinsic

---

## Example 6 - Intrinsic (Contract Violation)

**Bug Project:** rest-api-wrapper  
**Bug Title:** "Passing undefined parameter causes cryptic error deep in axios"  
**Bug Description:** When calling our wrapper function with undefined as a parameter, we get an unhelpful error message from deep in the axios library stack. The error doesn't indicate what's wrong. We should validate parameters before passing them to axios.  
**Labels:** bug, enhancement  
**Maintainer Comments:** "Good catch. We should add input validation before calling axios. The error is confusing because we're passing invalid data downstream."  
**Closing PR:** Yes - added parameter validation to check for undefined/null before calling axios

**Reasoning:**
**Definition Check:** Input validation missing -> Intrinsic candidate
**Maintainer Signal:** Maintainer confirms we need to fix this ("We should add input validation")
**PR/Commit Evidence:** PR adds validation logic in our wrapper code. This is a production code fix in our repo.
**Intrinsic Evidence Gate:** PASSES - PR modifies production code to add proper validation
**Contract Rule:** KEY - We are passing invalid input (undefined) to axios. We are violating axios's contract. This is OUR fault.
**Root Location:** While the error appears in axios (node_modules), the root cause is we're passing invalid data. We should validate first.
**Decision:** We're misusing the dependency by passing invalid input. This is our responsibility to fix.
**Confidence:** High - we're violating dependency's contract by passing invalid input

**Final Answer:** Intrinsic

---

## Example 7 - Unknown (Insufficient Information)

**Bug Project:** cli-tool  
**Bug Title:** "Crashes sometimes"  
**Bug Description:** "The tool crashes sometimes when I use it. Very frustrating. Please fix."  
**Labels:** bug  
**Maintainer Comments:** "I cannot reproduce this without more information. Please provide: (1) Node version, (2) exact command you're running, (3) error output, (4) steps to reproduce."  
**Closing PR:** None

**Reasoning:**
**Definition Check:** Potentially a real issue but insufficient information to classify
**Maintainer Signal:** Maintainer explicitly states "cannot reproduce without more information"
**PR/Commit Evidence:** No closing PR exists
**Information Completeness:** FAILS - No versions, no reproduction steps, no error messages, no logs. Just "crashes sometimes".
**Temporal Signals:** None
**Intrinsic Evidence Gate:** Cannot evaluate - no stack trace, no clear description of behavior

**Decision:** Despite "bug" label, there is absolutely insufficient information to determine:

- What is crashing
- When it crashes
- What the error is
- Whether it's internal, external, or user error

Without this basic information, classification is impossible.

**Confidence:** High - insufficient information despite bug label
**Final Answer:** Unknown

---

## Example 8 - Extrinsic (Build Tool Update)

**Bug Project:** babel-plugin  
**Bug Title:** "Plugin breaks with Babel 8: option 'loose' no longer supported"  
**Bug Description:** After upgrading Babel from 7 to 8, our plugin configuration throws an error: "Unknown option 'loose'". This option worked fine in Babel 7 and is part of our documented configuration.  
**Labels:** bug, dependencies  
**Maintainer Comments:** "Babel 8 removed several options including 'loose'. This is a documented breaking change. We'll need to update our plugin and docs."  
**Closing PR:** Yes - removed 'loose' option and updated plugin for Babel 8 compatibility

**Reasoning:**

**Definition Check:** External tool removed configuration option -> Extrinsic candidate
**Maintainer Signal:** Maintainer identifies external cause ("Babel 8 removed several options")
**PR/Commit Evidence:** PR removes deprecated option and updates plugin to work with Babel 8's new schema
**Temporal Signals:** STRONG - "After upgrading Babel 7 to 8". Version change mentioned. "worked fine in Babel 7".
**Contract Rule:** Our config was valid for Babel 7. Babel made a breaking change. We were NOT misusing the API.
**Dependency Bump Rule:** Repo likely has a commit bumping Babel 7 -> 8, but the root cause is Babel's breaking change
**Decision:** External tool (Babel) made a documented breaking change. Our plugin must adapt to new requirements.
**Confidence:** High - explicit external version change, maintainer confirmation

**Final Answer:** Extrinsic

---

# CRITICAL REMINDERS

Before finalizing your classification, verify:

- **Content analyzed FIRST** - Labels checked only for supporting evidence
- **Maintainer comments prioritized** - They have highest authority on root cause
- **Intrinsic Evidence Gate applied** - Don't select Intrinsic without positive evidence
- **Temporal signals identified** - Strong indicators of Extrinsic
- **Contract Rule applied** - Who violated the contract: us or them?
- **Test-only issues** - Classified as Not a Bug unless they expose production bugs
- **Questions/docs/features** - Always Not a Bug regardless of labels
- **Unknown only when necessary** - Use only when inference is truly impossible
- **Closing PR examined** - If it exists, rarely Unknown; classify what was fixed

---

# REASONING FORMAT

You must use this structure in your response:

**Reasoning:**
**Definition Check** - Identify which category definitions match the signals
**Maintainer Signal** - Note any authoritative maintainer comments about root cause
**PR/Commit Evidence** - If closing PR exists, describe what it changed and why
**Temporal Signals** - Check for version changes, upgrades, "works on X fails on Y" patterns
**Contract Rule** - If API usage involved, determine who violated the contract
**Information Completeness** - Assess if there's enough detail to classify confidently
**Final Answer:** - State your classification and the primary evidence supporting it
**Confidence:** High/Medium/Low

**Probability Distribution:**
After your reasoning, you MUST provide probability estimates for each category.
These should be decimal values between 0.0 and 1.0 that sum to 1.0.
Consider all evidence and uncertainty when assigning probabilities.

Format exactly as:

- Intrinsic: 0.XX
- Extrinsic: 0.XX
- Not a Bug: 0.XX
- Unknown: 0.XX

Guidelines for probability assignment:

- High confidence final answer: 0.70-0.95 for chosen class
- Medium confidence: 0.50-0.70 for chosen class
- Low confidence: 0.35-0.50 for chosen class, or spread across 2-3 classes
- Distribute remaining probability among plausible alternatives
- Unknown should get probability only if information is genuinely insufficient

YOU MUST END YOUR RESPONSE WITH EXACTLY THIS:

**Final Answer:** [Intrinsic / Extrinsic / Not a Bug / Unknown]

Examples:

- **Final Answer:** Intrinsic
- **Final Answer:** Not a Bug

DO NOT write "This is Intrinsic" - write "**Final Answer:** Intrinsic"
