# BUG RE-VERIFICATION - RECOVERY PASS (Bug vs Not a Bug)

You are a maintainer for the package this bug report is being submittied to. You must classify this report.

A prior stage in this pipeline labeled this issue **Not a Bug**. You are re-examining that decision to catch a **real defect that may have been wrongly dismissed**. Decide independently:

Does this report describe an **actual defect in the software** or not?

The prior verdict and its full reasoning are provided below (under "Prior pipeline assessment") as a **prior opinion that may be wrong**. Critique it - do not rubber-stamp it.

# WHAT MAKES IT A BUG (a defect may have been dismissed)

A **Bug** is a real defect: the package itself malfunctions in supported usage - it crashes, throws, produces wrong output, breaks a build, regresses, or a supported feature stops working. The cause may be the package's own code (**Intrinsic**) or an external change it is subject to (**Extrinsic**) - **both are Bugs**.

## Externally-triggered breakage (Extrinsic) - FLIP these

The package working before an external change and failing after it is a real defect (**Extrinsic**), even though the trigger is outside the package. These are frequently dismissed wrongly - **flip them to Bug** when the package's own behaviour, output, or build breaks after:

- **a new browser / browser version** - "click events broken on IE and Edge", a feature that works in Chrome but throws in Safari. A failure on a supported browser is the package's defect, not the browser's.
- **a new runtime / Node / engine version** - "`--watch` stopped working on Node v4", "fails to load on Node 11", a crash that appears only under a newer runtime.
- **a new major of a dependency / tool the package runs under** - a config, option, or input that worked before is now rejected ("Webpack 5: `configuration.node` has an unknown property `dgram`; it worked on Webpack 4"). Do **not** reframe this as "correct validation / working as designed" - it is a version-incompatibility the package must adapt to.
- **a dependency upgrade that breaks the package's own build or shipped output** (not merely a coverage/reporting check) - "an in-range update of X is breaking the build" where the build itself fails.
- **a new OS / platform** where the package's documented behaviour now misbehaves.

For these, **no closing PR or commit is expected** - the fix is often upstream, applied later, or never recorded. A maintainer saying "won't fix / upstream's problem" does **not** make a genuine malfunction a non-defect. When the deciding question "would fixing this require THIS package to change its code, config, or version handling?" is **yes**, flip to **Bug**.

# WHAT CORRECTLY STAYS NOT A BUG

Keep **Not a Bug** when the report is genuinely one of:

- a usage question, documentation, or a feature / behavior-change request ("should support/accept X", "make Y configurable")
- a lint / formatter rule firing or not firing per preference (a rule debate, not a crash)
- a type-definition gap / typings request
- user configuration / environment / unsupported setup
- intended / documented behavior
- a defect lying **entirely** in another project while this package behaves correctly

# DO NOT FLIP THESE (user-side / non-defects that read like failures)

These stay **Not a Bug** even with error-like wording. But do **not** let them swallow the Extrinsic FLIP cases above - an external **browser / runtime / dependency-major** change that breaks the package is a Bug, not "user environment".

- **The failure is the user's own code, config, or callback.** A wrong loader/option setup, an invalid config the package correctly rejects on the *same* version, a stack trace originating in the user's own callback or build script. The package itself behaves correctly given valid input. -> **Not a Bug.**
- **Pure CI coverage / reporting tooling.** A failing coverage/coveralls/greenkeeper *report* where the **shipped package still builds and works** and only the coverage badge is red. (A dep upgrade that breaks the actual **build** is Extrinsic - flip that.) -> **Not a Bug.**
- **Feature / behaviour-change request.** "should support X", "make Y configurable", and the resolution is "we could add an option" - a desired-but-absent capability, not a malfunction. -> **Not a Bug.**
- **Lint / formatter rule preference** with no crash and no provably invalid output. -> **Not a Bug.**
- **Defect lying entirely in another project** while this package passes correct data through and behaves correctly. -> **Not a Bug.**

Distinguish carefully: a failure tied to the **user's own shell/config/setup** (e.g. path-casing in their shell, a mis-set option) is Not a Bug; a failure tied to an **external browser/runtime/dependency version** is Extrinsic (Bug).

# DECISION

Flip to **Bug** when **either**:

- **(A) Intrinsic malfunction:** concrete evidence the package itself crashes, throws, breaks its build/install, or produces wrong output on valid, supported input; **or**
- **(B) Extrinsic breakage:** the package's own behaviour/output/build breaks after an external **browser / runtime / dependency-major / OS** change (the FLIP list above), and fixing it would require THIS package to adapt its code, config, or version handling.

**Keep Not a Bug** when the report is a user-side config/code issue, a feature/behaviour-change request, a lint preference, pure coverage-tooling noise, or a defect entirely in another project (the DO-NOT-FLIP list).

When the evidence is genuinely ambiguous between an **external-version/platform** failure (B) and a non-defect, lean toward **Bug** for browser/runtime/dependency-major breakage; lean toward **Not a Bug** for user-config/feature/lint. Default to the prior only when neither side is clear.

# EXAMPLES

## Example 1 - Bug (Extrinsic wrongly dismissed)

Prior verdict: Not a Bug ("an external tool is involved"). Report: after upgrading the bundler to a new major version, the package's build throws "unknown property"; it worked on the previous version.
**Re-judgment:** The package's own build now fails after an external version change - an Extrinsic defect. The external trigger does not make it a non-defect. -> **Bug**

## Example 2 - Not a Bug (correctly dismissed)

Prior verdict: Not a Bug. Report: "the linter should also flag this pattern" / "please add an option for X."
**Re-judgment:** A request for new/changed behavior; the package is not malfunctioning. -> **Not a Bug**

## Example 3 - Bug (browser-specific failure, Extrinsic)

Prior verdict: Not a Bug ("it's the browser's behaviour, not ours"). Report: "Click events broken on IE and Edge"; works in other browsers.
**Re-judgment:** The package's output is wrong on a supported browser; an external trigger does not excuse it. -> **Bug**

## Example 4 - Bug (config rejected after a dependency major, Extrinsic)

Prior verdict: Not a Bug ("the new version correctly rejects this; working as designed"). Report: "After upgrading to Webpack 5, `configuration.node` has an unknown property `dgram`; it worked on Webpack 4."
**Re-judgment:** A version-incompatibility the package must adapt to, not "working as designed". Do not reframe a regression as correct validation. -> **Bug**

## Example 5 - Not a Bug (CI coverage tooling only)

Prior verdict: Not a Bug. Report: "An in-range update of coveralls is breaking the build" - only the **coverage report** went red; the shipped package still builds and works.
**Re-judgment:** Pure coverage-tooling noise; the package itself is unaffected. (Contrast: a dep upgrade that breaks the actual build is Extrinsic.) -> **Not a Bug**

## Example 6 - Not a Bug (fix is to add an option)

Prior verdict: Not a Bug. Report: behavior the user dislikes; the resolution discussed is "we could make this optional / add a flag."
**Re-judgment:** The remedy is a new configurable option - a feature request, not a malfunction. -> **Not a Bug**

## Example 7 - Not a Bug (defect entirely elsewhere)

Prior verdict: Not a Bug. Report: data is corrupted, but the corruption occurs inside a sibling tool / the user's own callback; this package passes correct data through and returns what it is given.
**Re-judgment:** This package behaves correctly; there is nothing here to change. -> **Not a Bug**

# RESPONSE INSTRUCTIONS

Use this structure in your response:

**Reasoning:**
**Critique of prior verdict** - Did the prior assessment dismiss a real malfunction, and on what basis?
**Malfunction** - Does the package itself crash / throw / break its build / produce wrong output? Quote the evidence, or say "None."
**Extrinsic trigger** - Did the package break after an external **browser / runtime / dependency-major / OS** change (FLIP), or is the failure the user's own config/code/setup (keep)? Name which.
**Exclusions** - Is it genuinely a feature request / lint preference / pure coverage-tooling noise / defect entirely elsewhere?
**Confidence:** High/Medium/Low

Flip to **Bug** if there is an Intrinsic malfunction on valid input, **or** an Extrinsic browser/runtime/dependency-major breakage of the package. Keep **Not a Bug** for user-config/feature/lint/other-project cases.

YOU MUST END YOUR RESPONSE WITH EXACTLY THIS:

**Final Answer:** [Bug / Not a Bug]

Examples:

- **Final Answer:** Bug
- **Final Answer:** Not a Bug
