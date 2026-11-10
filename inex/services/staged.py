import re

VALIDITY_NAME = "Stage 1 - Bug vs Not a Bug"
RECOVER_NAME = "Recheck (recover) - Bug vs Not a Bug"
MAINTAINER_ROLES = ("OWNER", "MEMBER", "COLLABORATOR")
MAX_BODY_CHARS = 80000
MAX_PR_BODY_CHARS = 15000
MAX_COMMIT_MESSAGE_CHARS = 2000
MAX_MAINTAINER_COMMENT_CHARS = 20000
MAX_OTHER_COMMENT_CHARS = 8000
CONFIDENCE = re.compile(r"\*\*Confidence:\*\*\s*(High|Medium|Low)", re.IGNORECASE)
NUMBER = r"([0-9]*\.?[0-9]+)"
UPSTREAM_HEADER = (
    "---",
    "## Prior pipeline assessment (where this classification has reached)",
    "Earlier step(s) in this pipeline reached the verdict(s) and reasoning below. "
    "Treat them as prior opinions that may be wrong - re-judge independently; "
    "do not simply agree.",
    "",
)


def _value(mapping, key, default):
    value = mapping.get(key)
    return default if value is None else value


def _author(obj):
    info = obj.get("author")
    if not info:
        return "Unknown", "NONE"
    return _value(info, "username", "Unknown"), _value(info, "author_association", "NONE")


def _comment(i, comment, max_len, always_show_role):
    user, role = _author(comment)
    body = _value(comment, "body", "")
    date = _value(comment, "created_at", "")
    if len(body) > max_len:
        body = body[:max_len] + "\n[...truncated...]"
    role_indicator = f" [{role}]" if (always_show_role or role == "CONTRIBUTOR") else ""
    return f"**Comment {i}** by {user}{role_indicator} ({date}):\n{body}"


def _pr_details(pr):
    if not pr:
        return ""
    body = pr.get("body")
    shown = body[:MAX_PR_BODY_CHARS] if body else "No description"
    more = "..." if body and len(body) > MAX_PR_BODY_CHARS else ""
    return f"""
**Closing PR Details:**
- Title: {_value(pr, "title", "No title")}
- Merged: {_value(pr, "merged", False)}
- Files Changed: {_value(pr, "changed_files", 0)}
- Body: {shown}{more}
"""


def _commit_details(commit):
    if not commit:
        return ""
    message = commit if isinstance(commit, str) else _value(commit, "message", "No message")
    more = "..." if len(message) > MAX_COMMIT_MESSAGE_CHARS else ""
    return f"\n**Closing Commit:** {message[:MAX_COMMIT_MESSAGE_CHARS]}{more}"


def _comments(comments):
    maintainers = [c for c in comments if _author(c)[1] in MAINTAINER_ROLES]
    others = [c for c in comments if _author(c)[1] not in MAINTAINER_ROLES]
    maintainers.sort(key=lambda c: _value(c, "created_at", ""))
    others.sort(key=lambda c: _value(c, "created_at", ""))
    formatted = [
        _comment(i, c, MAX_MAINTAINER_COMMENT_CHARS, True) for i, c in enumerate(maintainers, 1)
    ]
    formatted += [
        _comment(i, c, MAX_OTHER_COMMENT_CHARS, False)
        for i, c in enumerate(others, len(maintainers) + 1)
    ]
    return "\n\n".join(formatted) if formatted else "No comments"


def format_issue(issue):
    author, role = _author(issue)
    labels = issue.get("labels") or []
    names = [label.get("name", "") for label in labels if label.get("name")]
    closed_by = issue.get("closed_by")
    closer = _value(closed_by, "username", "Unknown") if closed_by else "Unknown"
    body = _value(issue, "body", "No description provided")
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[..truncated for length..]"
    return f"""---
## Issue Data to Classify

**Project:** {issue["owner"]}/{issue["repo"]}
**Issue Number:** #{_value(issue, "number", "Unknown")}
**Title:** {_value(issue, "title", "No title")}

**Author:** {author} (Role: {role})
**Labels:** {", ".join(names) if names else "None"}
**Created:** {_value(issue, "created_at", "Unknown")}
**Closed:** {_value(issue, "closed_at", "Not closed")}
**Closed By:** {closer}

**Issue Body:**
{body}
{_pr_details(issue.get("closing_pr"))}{_commit_details(issue.get("closing_commit"))}

**Comments & Discussion:**
{_comments(_value(issue, "comments", []))}

---
"""


def upstream_context(entries):
    lines = list(UPSTREAM_HEADER)
    for name, verdict, reasoning in entries:
        lines.append(f"### {name}: **{verdict}**")
        lines.append((reasoning or "").strip() or "(no reasoning recorded)")
        lines.append("")
    return "\n".join(lines)


def build_prompt(guide_text, issue_context, prior=None):
    context = upstream_context(prior) + "\n" + issue_context if prior else issue_context
    return guide_text.strip() + "\n" + context


def read_verdict(labels, text):
    if not text:
        return None
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"\*\*Final\s*Answer:\*?\*?\s*({pattern})", text, re.IGNORECASE)
    if not match:
        return None
    raw = re.sub(r"\s+", " ", match.group(1).lower().strip())
    return {label.lower(): label for label in labels}.get(raw)


def read_confidence(text):
    match = CONFIDENCE.search(text or "")
    return match.group(1).capitalize() if match else None


def read_probabilities(labels, text):
    names = sorted(labels, key=len, reverse=True)
    pattern = rf"-\s*({'|'.join(re.escape(name) for name in names)}):\s*{NUMBER}"
    canonical = {label.lower(): label for label in labels}
    return {
        canonical[name.lower()]: float(value)
        for name, value in re.findall(pattern, text or "", re.IGNORECASE)
    }


def _record(stage, model, verdict, reply, labels):
    return {
        "stage": stage,
        "model": model,
        "verdict": verdict,
        "confidence": read_confidence(reply),
        "probabilities": read_probabilities(labels, reply) or None,
        "response": reply,
    }


def run_pipeline(stages, issue, ask, models, records=None):
    records = [] if records is None else records
    context = format_issue(issue)
    not_a_bug = stages.validity.labels[1]
    reply, verdict = ask(
        build_prompt(stages.validity.guide_text, context), "stage", stages.validity.labels
    )
    records.append(_record("validity", models["stage"], verdict, reply, stages.validity.labels))
    prior = [(VALIDITY_NAME, verdict, reply)]
    effective = verdict
    if verdict == not_a_bug and stages.recover is not None:
        recover = stages.recover
        reply, effective = ask(
            build_prompt(recover.guide_text, context, prior), "judge", recover.labels
        )
        records.append(_record("recover", models["judge"], effective, reply, recover.labels))
        prior = [*prior, (RECOVER_NAME, effective, reply)]
    if effective == not_a_bug:
        return not_a_bug, records
    origin = stages.origin
    reply, verdict = ask(build_prompt(origin.guide_text, context, prior), "stage", origin.labels)
    records.append(_record("origin", models["stage"], verdict, reply, origin.labels))
    return verdict, records
