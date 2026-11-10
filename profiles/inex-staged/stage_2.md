# INTRINSIC vs EXTRINSIC CLASSIFICATION GUIDE

This issue has been determined to be a **real defect**. Your task is to determine the **origin** of the defect under the **InEx-Bug** taxonomy:

- **Intrinsic** - the defect originates within this package's own code.
- **Extrinsic** - the defect is caused by a change or incompatibility external to this package.

You must reason carefully, prioritize maintainer signals, and classify based on the strongest evidence.

---

# DEFINITIONS (InEx-Bug)

Classify by **where the defect originated**, not by where the fix lands.

## INTRINSIC - "The bug originates within the package to which the report belongs."

The defect exists in this package's own code, **independent of external factors**.

Includes:

- Logic/validation/exception/flow errors in our source code
- Missing null/undefined checks; incorrect async/promise usage
- Our SQL/migrations/schema/scripts wrong or incomplete
- Regressions caused by our own commits in this package
- Incorrect or incomplete package.json fields (exports, peer deps, entry points) that are wrong on their own terms
- Transformation/formatting/transpilation bugs caused by our own logic
- Wrong behavior for valid, supported inputs due to our own code

---

## EXTRINSIC - "The bug is caused by changes or incompatibilities external to the project."

Examples (from the InEx-Bug definition): **dependency upgrades, operating system differences, or browser-specific behavior.**

**Signature:** the package's code **functioned correctly under previous conditions, but it fails following an external modification.**

> **Key rule (maintainer perspective):** An issue is **Extrinsic even if resolving it required a local code change**, provided the defect _originated externally_. The fix landing in our repo does NOT make it Intrinsic.

Includes:

- Dependency / Node / browser / toolchain upgrades causing breaking changes
- npm/yarn/pnpm resolution changes; transitive dependency errors
- OS / platform / runtime behavior differences (e.g., works on one OS/Node/browser, fails on another)
- Upstream API deprecations/removals/publications while we used them correctly
- Node/V8/ESM behavior changes; build toolchain updates (Webpack/Babel/TS/Rollup/esbuild/Vite)
- Parser/formatter/linter behavior changes from upstream
- Issues appearing after an external version change, even when the fix is a local adaptation

---

# DECISION PROCEDURE (apply in order)

**Intrinsic is NOT the default** - it must be earned with affirmative evidence of an internal defect. Work through these steps:

1. **Is any external factor named or implied as the trigger?** A specific dependency/runtime/browser/OS/toolchain version, "since X updated", "works on A fails on B", "only on Firefox/Windows/Node 18", an upstream deprecation/publication, a transitive dependency error. to **Lean Extrinsic.**
2. **Does the evidence affirmatively show the defect is in THIS repo's own logic?** (See the Intrinsic Evidence Gate.) Only if it passes to **Intrinsic.**
3. **If both an external trigger is present AND there is no clear proof the root cause is our own logic to choose Extrinsic.**

**Tie-breaker:** When genuinely uncertain between Intrinsic and Extrinsic, and an external version/dependency/runtime/browser is involved, choose **Extrinsic**. Do not default to Intrinsic.

---

# THE ORIGIN TEST

> **Would this exact defect exist WITHOUT the external change?**
>
> - **No** - it only fails _because_ a dependency/tool/runtime/browser changed, was upgraded, or behaves newly; it worked before to **EXTRINSIC.**
> - **Yes** - it fails regardless of any external change, purely due to our own logic on valid input to **INTRINSIC.**

**Do NOT confuse "the package must change to fix it" with "the defect is Intrinsic."** A package needing a local fix to _adapt_ to an external change is the normal Extrinsic case. The reasoning **"the package failed to handle X / it's the package's responsibility to support X / it violated its own contract to work"** is **NOT evidence of Intrinsic origin** - that statement is true of _every_ Extrinsic bug. A package failing to handle a **new** external condition (a dependency's new version, new input from an upgraded tool, a runtime/browser change) is **Extrinsic**.

---

# CORE DECISION PRINCIPLES

- **Maintainer comments are the strongest signal**
  Owners/Members/Collaborators often state the real cause directly.

- **Closing PR/commit determines root cause**
  Does the fix patch our own logic, or adapt to an external change? Absence of a PR is neutral - judge the described cause.

- **Temporal & environment signals point to Extrinsic**
  "After upgrading X", "works on Y fails on Z", "no longer works with", "since [dep] vX", browser/OS/runtime-specific failures, errors surfacing only after an upstream release - all strongly indicate **Extrinsic**.

- **Contract Rule (narrow)** - applies ONLY to _static_ misuse with no external change involved:
  - We **statically** misuse a dependency's CURRENT, unchanged API (pass the wrong type, call it incorrectly) and it would fail regardless of any version change to **Intrinsic**
  - The dependency/platform/runtime **changed** its API/behavior/version and we now fail to **Extrinsic**
  - It does NOT apply to "the package violated its own implicit contract to work / should have handled the external thing" - that is not the Contract Rule, and it is not evidence of Intrinsic origin (apply the Origin Test).

- **Intrinsic is earned, not assumed** - without positive evidence of an internal defect, and with any external trigger present, prefer Extrinsic.

---

# OPERATIONAL RULES

## 1. INTRINSIC EVIDENCE GATE

Before choosing Intrinsic, there must be clear evidence that the defect originates inside this repository's own logic.

Intrinsic should only be selected when one or more of the following hold:

- A maintainer (OWNER / MEMBER / COLLABORATOR) explicitly states the problem is caused by this repository's code.
- A stack trace or reproduction clearly shows failure inside this repo's own source code (not upstream, not user code).
- The fix PR explicitly corrects an internal logic/behavior error - not compatibility updates, refactors, config cleanups, feature additions, or upstream workaround patches.
- Valid supported inputs produce incorrect behavior due to our logic, not external changes.

If the evidence does NOT clearly show the bug originates in this repo's logic to Do NOT choose Intrinsic.

---

## 2. TEMPORAL SIGNALS FOR EXTRINSIC

These phrases strongly indicate Extrinsic:

- "after upgrading [dependency/Node/tool]"
- "after npm update" / "after updating dependencies"
- "since version X" / "started when upgrading to..."
- "works on [X], fails on [Y]"
- "breaking change in [dependency]"
- "worked yesterday, broken today" (with no code changes)

**When these signals are present, the defect is Extrinsic unless there is affirmative evidence of an internal defect.**

---

## 3. CONTRACT RULE (narrow - static misuse only)

This rule decides ONLY the case of _static misuse of an unchanged API_. It does NOT cover "the package failed to handle / adapt to an external change."

- If **we statically misuse** a dependency's CURRENT, unchanged API (wrong type, wrong call) such that it would fail **with or without** any version change to **Intrinsic**
- If **the dependency/platform/runtime changed** its API/behavior/version and we now fail to **Extrinsic**

Example: We pass `undefined` to a library that has always required a string, no version change to Intrinsic (our fault, fails regardless).
Example: A library **removes/changes** an endpoint we were using correctly to Extrinsic (their change).
Example: Our code "fails to handle" a dependency's **new** major-version output to Extrinsic (Origin Test: it worked before the upgrade). This is NOT "we violated a contract."

**Do not stretch "we violated the contract" to cover a failure-to-adapt** - run the Origin Test instead.

---

## 4. DEPENDENCY BUMP CLARIFICATION

A version bump commit does NOT automatically make a bug Intrinsic.

**Scenario:** The only change in this repo was `chore: bump webpack 4.0 -> 5.0` and the breakage comes from webpack 5's breaking changes.

**Classification:** This is **Extrinsic** - the root cause is the external breaking change, even though there's a commit in this repo.

---

## 5. SIGNAL PRIORITY ORDER

When signals conflict, prioritize in this order:

1. **Maintainer comments** (highest authority)
2. **Closing PR/commit** (what did the fix actually change - our logic or external adapter?)
3. **Actual content** (title, body, repro, stack trace)
4. **Labels** (weakest signal)

---

# EXAMPLES

## Example 1 - Intrinsic (Logic Bug)

**Title:** "Validation allows negative values when min is 0"
**Description:** Setting min: 0 should reject negative numbers but -5 passes validation.
**Closing PR:** Yes - fixed comparison logic in production code.

**Reasoning:**
**Maintainer Signal:** No explicit comment, but closing PR provides strong evidence.
**PR/Commit Evidence:** PR modifies internal validation logic in production code. The fix corrects comparison operators.
**Intrinsic Evidence Gate:** PASSES - Closing PR modifies this repo's production code to fix incorrect behavior.
**Temporal Signals:** None - no "after upgrade" or version change mentioned.
**Contract Rule:** Not applicable - this is internal logic, not API usage.
**Decision:** Clear internal logic bug. Comparison operators in our validator were wrong. PR fixes production code in this repo.
**Confidence:** High

**Final Answer:** Intrinsic

---

## Example 2 - Intrinsic (API Violation)

**Title:** "Passing undefined parameter causes cryptic error deep in axios"
**Description:** When calling our wrapper with undefined, we get a confusing error from deep in the axios stack. We should validate before passing to axios.
**Maintainer Comments:** "Good catch. We should add input validation before calling axios."
**Closing PR:** Yes - added parameter validation before calling axios.

**Reasoning:**
**Maintainer Signal:** Maintainer confirms we need to fix this.
**PR/Commit Evidence:** PR adds validation logic in our wrapper code. Production code fix in our repo.
**Intrinsic Evidence Gate:** PASSES - PR modifies production code to add proper validation.
**Contract Rule:** KEY - We are passing invalid input (undefined) to axios. We are violating axios's contract. This is OUR fault.
**Temporal Signals:** None - no version changes mentioned.
**Decision:** We're misusing the dependency by passing invalid input. Our responsibility to fix.
**Confidence:** High

**Final Answer:** Intrinsic

---

## Example 3 - Extrinsic (Dependency Breaking Change)

**Title:** "Build fails after webpack 4 to 5 upgrade"
**Description:** After upgrading webpack to 5.0, builds fail with "unknown config property". Same config worked in webpack 4.
**Maintainer Comments:** "Webpack 5 changed the config schema. We need to update our plugin config."
**Closing PR:** Yes - updated plugin configuration for webpack 5 compatibility.

**Reasoning:**
**Maintainer Signal:** Maintainer explicitly identifies external cause ("Webpack 5 changed...").
**PR/Commit Evidence:** PR only updates configuration to adapt to webpack 5's new schema. No internal logic bugs fixed.
**Temporal Signals:** STRONG - "after webpack upgrade", clear version change.
**Contract Rule:** Our config was valid for webpack 4. Webpack changed its requirements. We were NOT misusing the API.
**Dependency Bump Rule:** Root cause is webpack's breaking change, not our logic to Still Extrinsic.
**Decision:** External breaking change. Our code must adapt.
**Confidence:** High

**Final Answer:** Extrinsic

---

## Example 4 - Extrinsic (Runtime Version Change)

**Title:** "Works on Node 16, fails on Node 18 with ERR_REQUIRE_ESM"
**Description:** Builds work on Node 16 LTS. After upgrading to Node 18, we get module resolution errors. No code changes.
**Maintainer Comments:** "Node 18 has stricter ESM handling. We need to update our package.json module structure."
**Closing PR:** Yes - updated package.json for Node 18 ESM compatibility.

**Reasoning:**
**Maintainer Signal:** Maintainer identifies external cause ("Node 18 has stricter ESM handling").
**PR/Commit Evidence:** PR updates module configuration to work with Node 18. Adapting to external change.
**Temporal Signals:** STRONG - "Works on X, fails on Y", "after upgrading to Node 18".
**Contract Rule:** We weren't violating Node 16's rules. Node 18 introduced stricter requirements.
**Decision:** External runtime version changed behavior. Our code must adapt.
**Confidence:** High

**Final Answer:** Extrinsic

---

## Example 5 - Extrinsic (Build Tool Update)

**Title:** "Plugin breaks with Babel 8: option 'loose' no longer supported"
**Description:** After upgrading Babel 7 to 8, our plugin throws "Unknown option 'loose'". This option was documented and worked in Babel 7.
**Maintainer Comments:** "Babel 8 removed several options including 'loose'. This is a documented breaking change."
**Closing PR:** Yes - removed deprecated option and updated for Babel 8 compatibility.

**Reasoning:**
**Maintainer Signal:** Maintainer identifies external cause ("Babel 8 removed several options").
**PR/Commit Evidence:** PR removes deprecated option, updates plugin for Babel 8's new schema.
**Temporal Signals:** STRONG - "After upgrading Babel 7 to 8", "worked fine in Babel 7".
**Contract Rule:** Our config was valid for Babel 7. Babel made a breaking change. We were NOT misusing the API.
**Decision:** External tool made a documented breaking change. Our plugin must adapt.
**Confidence:** High

**Final Answer:** Extrinsic

---

# CRITICAL REMINDERS

Before finalizing your classification:

- **Maintainer comments prioritized** - they have highest authority on root cause
- **Intrinsic Evidence Gate** - don't select Intrinsic without positive evidence of internal defect
- **Temporal signals** - "after upgrade", "works on X fails on Y" strongly indicate Extrinsic
- **Contract Rule** - who violated the contract, us or the dependency?
- **Dependency bump** - a version bump commit does NOT make it Intrinsic
- **Closing PR** - does it patch our logic, or adapt to an external change? (Absence is neutral.)
- **Intrinsic is earned** - without affirmative internal-defect evidence, and with an external trigger present, choose **Extrinsic**. Do not default to Intrinsic.

---

# REASONING FORMAT

You must use this structure in your response:

**Reasoning:**
**External Trigger** - Is any external dependency/runtime/browser/OS/toolchain version or change named or implied? (If yes, lean Extrinsic.)
**Maintainer Signal** - Note any authoritative maintainer comments about root cause
**PR/Commit Evidence** - What did the fix change - our logic or an adaptation to external change? (Absence of a PR is neutral.)
**Temporal Signals** - Check for version changes, upgrades, "works on X fails on Y", browser/OS-specific failures
**Intrinsic Evidence Gate** - Is there clear AFFIRMATIVE evidence the defect originates in this repo's own logic? (If not, do not pick Intrinsic.)
**Contract Rule** - If API usage involved, who violated the contract: us or the dependency?
**Decision** - State your classification. If uncertain and an external trigger is present, choose Extrinsic.
**Confidence:** High/Medium/Low

YOU MUST END YOUR RESPONSE WITH EXACTLY THIS:

**Final Answer:** [Intrinsic / Extrinsic]

Examples:

- **Final Answer:** Intrinsic
- **Final Answer:** Extrinsic
