import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from inex.errors import InexError
from inex.services.progress import advance, finish

logger = logging.getLogger(__name__)
progress = logging.getLogger("inex.progress")

GRAPHQL_URL = "https://api.github.com/graphql"
COUNT_QUERY = (
    "query($owner: String!, $name: String!, $states: [IssueState!]) "
    "{ repository(owner: $owner, name: $name) { issues(states: $states) { totalCount } } }"
)
CLOSED_STATE = "closed"
CLOSED_STATES = ["CLOSED"]
PER_PAGE = 100
REQUEST_TIMEOUT_SECONDS = 30
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
RATE_LIMIT_BUFFER_SECONDS = 1
NETWORK_ATTEMPTS = 4
NETWORK_RETRY_FIRST_WAIT_SECONDS = 2
HTTP_SERVER_ERROR = 500
CLOSE_EVENT_WINDOW_SECONDS = 86400
REFERENCE_WINDOW_SECONDS = 604800
MAX_COMMIT_FILES = 20
PROGRESS_EVERY = 10


def _object(mapping, key):
    return (mapping or {}).get(key) or {}


@dataclass(frozen=True)
class CountResult:
    owner: str
    repo: str
    issue_count: int
    stored: int
    closed: bool = False


@dataclass(frozen=True)
class CollectResult:
    collected: int
    skipped: int
    total: int


@dataclass(frozen=True)
class SpecificResult:
    project_id: int
    issue_ids: tuple
    collected: int
    skipped: int
    failed: int


class CollectionService:
    def __init__(self, store, github_token):
        self.store = store
        self.github_token = github_token
        self.headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        }

    def _require_token(self):
        if not self.github_token:
            raise InexError(
                "github_token_missing",
                "No GitHub token is configured. Set GITHUB_TOKEN in .env.",
            )

    def _github_failure(self, error, owner, repo):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status == HTTP_NOT_FOUND:
            return InexError(
                "repo_not_found", f"{owner}/{repo} was not found on GitHub.", owner=owner, repo=repo
            )
        if status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            return InexError(
                "github_auth_failed",
                f"GitHub refused the request (HTTP {status}). Check that GITHUB_TOKEN is valid "
                "and can read this repository.",
                status=status,
            )
        if isinstance(error, requests.ConnectionError | requests.Timeout):
            return InexError(
                "network_error", "Could not reach GitHub. Check the network connection."
            )
        return InexError("github_error", f"GitHub request failed: {error}")

    def _stored(self, owner, repo, closed=False):
        project_id = self.store.project_id(owner, repo)
        if project_id is None:
            return 0
        return self.store.issue_count(project_id, state=CLOSED_STATE if closed else None)

    def count(self, owner, repo, *, closed=False) -> CountResult:
        self._require_token()
        try:
            r = requests.post(
                GRAPHQL_URL,
                headers=self.headers,
                json={"query": COUNT_QUERY, "variables": self._countVariables(owner, repo, closed)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            r.raise_for_status()
        except requests.RequestException as error:
            raise self._github_failure(error, owner, repo) from error
        repository = (r.json().get("data") or {}).get("repository")
        if not repository:
            raise InexError(
                "repo_not_found", f"{owner}/{repo} was not found on GitHub.", owner=owner, repo=repo
            )
        return CountResult(
            owner,
            repo,
            repository["issues"]["totalCount"],
            self._stored(owner, repo, closed),
            closed,
        )

    @staticmethod
    def _countVariables(owner, repo, closed):
        variables = {"owner": owner, "name": repo}
        if closed:
            variables["states"] = CLOSED_STATES
        return variables

    def parseTs(self, s):
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    @staticmethod
    def _transient(error):
        if isinstance(error, requests.ConnectionError | requests.Timeout):
            return True
        status = getattr(getattr(error, "response", None), "status_code", None)
        return isinstance(error, requests.HTTPError) and (status or 0) >= HTTP_SERVER_ERROR

    def get(self, url, hdrs=None):
        if hdrs is None:
            hdrs = self.headers
        attempt = 1
        while True:
            try:
                r = requests.get(url, headers=hdrs, timeout=REQUEST_TIMEOUT_SECONDS)
                if (
                    r.status_code == HTTP_FORBIDDEN
                    and r.headers.get("X-RateLimit-Remaining") == "0"
                ):
                    reset = int(r.headers.get("X-RateLimit-Reset", 0))
                    wait = max(0, reset - int(time.time()) + RATE_LIMIT_BUFFER_SECONDS)
                    logger.warning("Github Rate limit reached, sleeping for %d seconds", wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as error:
                if attempt >= NETWORK_ATTEMPTS or not self._transient(error):
                    raise
                wait = NETWORK_RETRY_FIRST_WAIT_SECONDS * 2 ** (attempt - 1)
                logger.warning("GitHub request failed (%s); retrying in %d seconds", error, wait)
                time.sleep(wait)
                attempt += 1

    def fetch(self, url):
        return self.get(url, self.headers).json()

    def fetchPaginated(self, url, extra_headers=None):
        hdrs = {**self.headers, **(extra_headers or {})}
        items = []
        page = 1
        while True:
            sep = "&" if "?" in url else "?"
            data = self.get(f"{url}{sep}per_page={PER_PAGE}&page={page}", hdrs).json()
            if not data:
                return items
            items.extend(data)
            if len(data) < PER_PAGE:
                return items
            page += 1

    def fetchPrDetails(self, owner, repo, prNum):
        try:
            pr = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/pulls/{prNum}")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == HTTP_NOT_FOUND:
                return None
            raise
        try:
            revs = self.fetchPaginated(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{prNum}/reviews"
            )
        except Exception:
            revs = []

        reviewers = set()
        revStates = {"approved": 0, "changes_requested": 0, "commented": 0, "dismissed": 0}

        for rv in revs:
            uname = _object(rv, "user").get("login")
            if uname:
                reviewers.add(uname)
            st = rv.get("state", "").lower()
            if st in revStates:
                revStates[st] += 1

        prAuthor = pr.get("user") or {}
        mergedBy = pr.get("merged_by") or {}

        return {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "html_url": pr.get("html_url"),
            "merged": bool(pr.get("merged_at")),
            "merged_at": pr.get("merged_at"),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "closed_at": pr.get("closed_at"),
            "state": pr.get("state"),
            "body": pr.get("body"),
            "author": {
                "username": prAuthor.get("login"),
                "id": prAuthor.get("id"),
            },
            "merged_by": {
                "username": mergedBy.get("login"),
                "id": mergedBy.get("id"),
            }
            if mergedBy
            else None,
            "commits": pr.get("commits"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "total_changes": (pr.get("additions") or 0) + (pr.get("deletions") or 0),
            "changed_files": pr.get("changed_files"),
            "review_comments": pr.get("review_comments"),
            "comments": pr.get("comments"),
            "unique_reviewers": len(reviewers),
            "reviewer_usernames": sorted(list(reviewers)),
            "total_reviews": len(revs),
            "approved_count": revStates["approved"],
            "changes_requested_count": revStates["changes_requested"],
            "commented_count": revStates["commented"],
            "head_ref": _object(pr, "head").get("ref"),
            "base_ref": _object(pr, "base").get("ref"),
            "head_sha": _object(pr, "head").get("sha"),
            "merge_commit_sha": pr.get("merge_commit_sha"),
        }

    def fetchCommitDetails(self, owner, repo, sha):
        c = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}")
        stats = _object(c, "stats")
        auth = _object(_object(c, "commit"), "author")
        comm = _object(_object(c, "commit"), "committer")

        return {
            "sha": c.get("sha"),
            "message": _object(c, "commit").get("message"),
            "html_url": c.get("html_url"),
            "author": {"date": auth.get("date")},
            "committer": {"date": comm.get("date")},
            "github_author": {
                "username": _object(c, "author").get("login"),
                "id": _object(c, "author").get("id"),
            }
            if c.get("author")
            else None,
            "github_committer": {
                "username": _object(c, "committer").get("login"),
                "id": _object(c, "committer").get("id"),
            }
            if c.get("committer")
            else None,
            "additions": stats.get("additions"),
            "deletions": stats.get("deletions"),
            "total_changes": stats.get("total"),
            "changed_files": len(c.get("files", [])),
            "files": [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "changes": f.get("changes"),
                }
                for f in c.get("files", [])[:MAX_COMMIT_FILES]
            ],
        }

    def calcTimestamps(self, createdAt, closedAt, comments):
        m = {
            "time_to_close_seconds": None,
            "time_to_first_response_seconds": None,
            "time_open_seconds": None,
        }

        if createdAt and closedAt:
            m["time_to_close_seconds"] = int((closedAt - createdAt).total_seconds())

        if createdAt and comments:
            firstComment = min(comments, key=lambda c: c.get("created_at", "9999"))
            fcTime = self.parseTs(firstComment.get("created_at"))
            if fcTime:
                if createdAt.tzinfo and not fcTime.tzinfo:
                    fcTime = fcTime.replace(tzinfo=UTC)
                elif not createdAt.tzinfo and fcTime.tzinfo:
                    createdAt = createdAt.replace(tzinfo=UTC)
                m["time_to_first_response_seconds"] = int((fcTime - createdAt).total_seconds())

        if createdAt:
            endTime = closedAt if closedAt else datetime.now(UTC)
            if not createdAt.tzinfo:
                createdAt = createdAt.replace(tzinfo=UTC)
            m["time_open_seconds"] = int((endTime - createdAt).total_seconds())

        return m

    def calcParticipants(self, authorLogin, comments):
        parts = set()
        maints = set()

        if authorLogin:
            parts.add(authorLogin)

        for c in comments:
            uname = _object(c, "user").get("login")
            assoc = c.get("author_association", "")
            if uname:
                parts.add(uname)
                if assoc in ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"]:
                    maints.add(uname)

        return {
            "unique_participants": len(parts),
            "unique_maintainers": len(maints),
            "maintainer_involved": len(maints) > 0,
        }

    def calcReopenMetrics(self, state, createdAt, closedAt, events):
        m = {
            "was_reopened": False,
            "reopen_count": 0,
            "time_to_reopen_seconds": None,
            "final_resolution_time_seconds": None,
            "reopen_timestamps": [],
        }

        closedEvts = [e for e in events if e.get("event") == "closed"]
        reopenedEvts = [e for e in events if e.get("event") == "reopened"]

        if not reopenedEvts:
            return m

        m["was_reopened"] = True
        m["reopen_count"] = len(reopenedEvts)
        m["reopen_timestamps"] = [e.get("created_at") for e in reopenedEvts]

        if closedEvts and reopenedEvts:
            firstClose = min(closedEvts, key=lambda e: e.get("created_at", ""))
            firstReopen = min(reopenedEvts, key=lambda e: e.get("created_at", ""))
            fcTime = self.parseTs(firstClose.get("created_at"))
            frTime = self.parseTs(firstReopen.get("created_at"))
            if fcTime and frTime:
                m["time_to_reopen_seconds"] = int((frTime - fcTime).total_seconds())

        if state == "closed" and createdAt and closedAt:
            m["final_resolution_time_seconds"] = int((closedAt - createdAt).total_seconds())

        return m

    def tryPr(self, owner, repo, prNum, createdTime, closedTime, window):
        prM = self.fetchPrDetails(owner, repo, prNum)
        if not prM or not prM.get("merged"):
            return None
        prMergedTime = self.parseTs(prM.get("merged_at"))
        if not prMergedTime or not createdTime or not closedTime:
            return None
        if prMergedTime < createdTime:
            return None
        if abs((prMergedTime - closedTime).total_seconds()) > window:
            return None
        return prM

    def findClosingMethod(self, owner, repo, num, createdAt, closedAt, events):
        if not closedAt:
            return None, None

        issueCreatedTime = self.parseTs(createdAt)
        issueClosedTime = self.parseTs(closedAt)

        closedEvts = [e for e in events if e.get("event") == "closed"]
        if not closedEvts:
            return None, None

        closedEvt = max(closedEvts, key=lambda e: e.get("created_at", ""))

        src = _object(closedEvt, "source")
        if src.get("type") == "issue":
            prNum = _object(src, "issue").get("number")
            if prNum:
                prM = self.tryPr(
                    owner,
                    repo,
                    prNum,
                    issueCreatedTime,
                    issueClosedTime,
                    CLOSE_EVENT_WINDOW_SECONDS,
                )
                if prM:
                    return prM, None

        crossRefs = [e for e in events if e.get("event") == "cross-referenced"]
        for ref in sorted(crossRefs, key=lambda e: e.get("created_at", ""), reverse=True):
            refSrc = _object(ref, "source")
            if refSrc.get("type") == "issue":
                prNum = _object(refSrc, "issue").get("number")
                if prNum:
                    prM = self.tryPr(
                        owner,
                        repo,
                        prNum,
                        issueCreatedTime,
                        issueClosedTime,
                        REFERENCE_WINDOW_SECONDS,
                    )
                    if prM:
                        return prM, None

        refEvts = [e for e in events if e.get("event") == "referenced"]
        for ref in sorted(refEvts, key=lambda e: e.get("created_at", ""), reverse=True):
            refSrc = _object(ref, "source")
            if refSrc.get("type") == "issue":
                prNum = _object(refSrc, "issue").get("number")
                if prNum:
                    prM = self.tryPr(
                        owner,
                        repo,
                        prNum,
                        issueCreatedTime,
                        issueClosedTime,
                        REFERENCE_WINDOW_SECONDS,
                    )
                    if prM:
                        return prM, None

            commitId = ref.get("commit_id")
            if commitId:
                try:
                    prs = self.fetch(
                        f"https://api.github.com/repos/{owner}/{repo}/commits/{commitId}/pulls"
                    )
                    if prs:
                        prM = self.tryPr(
                            owner,
                            repo,
                            prs[0].get("number"),
                            issueCreatedTime,
                            issueClosedTime,
                            REFERENCE_WINDOW_SECONDS,
                        )
                        if prM:
                            return prM, None
                except Exception:
                    continue

        commitSha = closedEvt.get("commit_id")
        commitUrl = closedEvt.get("commit_url")

        if not commitSha and commitUrl:
            parts = commitUrl.rstrip("/").split("/")
            if len(parts) > 0:
                commitSha = parts[-1]

        if commitSha:
            try:
                prs = self.fetch(
                    f"https://api.github.com/repos/{owner}/{repo}/commits/{commitSha}/pulls"
                )
                if prs:
                    prM = self.tryPr(
                        owner,
                        repo,
                        prs[0].get("number"),
                        issueCreatedTime,
                        issueClosedTime,
                        REFERENCE_WINDOW_SECONDS,
                    )
                    if prM:
                        return prM, None
                else:
                    try:
                        return None, self.fetchCommitDetails(owner, repo, commitSha)
                    except requests.exceptions.HTTPError:
                        pass

            except Exception:
                pass

        return None, None

    def buildComments(self, comments, format_ts=False):
        if not comments:
            return ""
        blocks = []
        for c in sorted(comments, key=lambda x: x.get("created_at", "")):
            ts = c.get("created_at", "Unknown")
            auth = _object(c, "author")
            uname = auth.get("username") or "unknown"
            assoc = auth.get("author_association", "NONE")
            body = c.get("body", "")
            if format_ts:
                tsObj = self.parseTs(ts)
                ts = tsObj.strftime("%Y-%m-%d %H:%MZ") if tsObj else "0000-00-00 00:00Z"
            blocks.append(f"[{ts}] [{assoc}] {uname}:\n{body}")
        return "\n\n---\n\n".join(blocks)

    def collectIssue(self, owner, repo, num):
        issue = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/issues/{num}")

        if issue.get("pull_request"):
            return None

        rawComments = self.fetchPaginated(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/comments"
        )
        timeline = self.fetchPaginated(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/timeline",
            {"Accept": "application/vnd.github.mockingbird-preview+json"},
        )

        commentsData = []
        maintComments = []

        for c in rawComments:
            obj = {
                "id": c.get("id"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
                "author": {
                    "username": _object(c, "user").get("login"),
                    "id": _object(c, "user").get("id"),
                    "author_association": c.get("author_association"),
                },
                "body": c.get("body"),
            }
            commentsData.append(obj)
            assoc = c.get("author_association", "")
            if assoc in ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"]:
                maintComments.append(obj)

        createdAt = self.parseTs(issue.get("created_at"))
        closedAt = self.parseTs(issue.get("closed_at"))
        authorLogin = _object(issue, "user").get("login")

        tMetrics = self.calcTimestamps(createdAt, closedAt, rawComments)
        pMetrics = self.calcParticipants(authorLogin, rawComments)
        rMetrics = self.calcReopenMetrics(issue.get("state"), createdAt, closedAt, timeline)

        closingPr = None
        closingCommit = None

        if issue.get("state") == "closed":
            closingPr, closingCommit = self.findClosingMethod(
                owner, repo, num, issue.get("created_at"), issue.get("closed_at"), timeline
            )

        commentsMd = self.buildComments(commentsData)
        commentsText = self.buildComments(commentsData, format_ts=True)

        labels = [
            {
                "name": label.get("name"),
                "description": label.get("description"),
                "color": label.get("color"),
            }
            for label in issue.get("labels", [])
        ]
        assignees = [
            {"username": u.get("login"), "id": u.get("id")} for u in issue.get("assignees", [])
        ]

        ms = issue.get("milestone")
        milestone = (
            {
                "number": ms.get("number"),
                "title": ms.get("title"),
                "state": ms.get("state"),
                "due_on": ms.get("due_on"),
            }
            if ms
            else None
        )

        closedBy = issue.get("closed_by")

        return {
            "owner": owner,
            "repo": repo,
            "number": issue.get("number"),
            "id": issue.get("id"),
            "url": issue.get("html_url"),
            "title": issue.get("title"),
            "body": issue.get("body") or "",
            "state": issue.get("state"),
            "state_reason": issue.get("state_reason"),
            "locked": issue.get("locked"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "closed_at": issue.get("closed_at"),
            "timestamp_metrics": tMetrics,
            "participant_metrics": pMetrics,
            "reopen_metrics": rMetrics,
            "author": {
                "username": _object(issue, "user").get("login"),
                "id": _object(issue, "user").get("id"),
                "author_association": issue.get("author_association"),
            },
            "closed_by": {"username": closedBy.get("login"), "id": closedBy.get("id")}
            if closedBy
            else None,
            "labels": labels,
            "assignees": assignees,
            "milestone": milestone,
            "comments_count": issue.get("comments"),
            "comments": commentsData,
            "comments_md": commentsMd,
            "comments_text": commentsText,
            "maintainer_comments": maintComments,
            "closing_pr": closingPr,
            "closing_commit": closingCommit,
        }

    def collect_specific(self, owner, repo, numbers) -> SpecificResult:
        self._require_token()
        db = self.store
        logger.info("Collecting %d specific issues for %s/%s", len(numbers), owner, repo)
        projectId = db.save_project(owner, repo)
        collected, skipped, failed = 0, 0, 0
        issueIds = []
        task = f"Collecting {owner}/{repo}"

        try:
            for num in numbers:
                existing = db.issue_id(projectId, num)
                if existing:
                    issueIds.append(existing)
                    skipped += 1
                else:
                    issueData = self._fetchListed(owner, repo, num)
                    if issueData is None:
                        failed += 1
                    else:
                        issueIds.append(db.save_issue(projectId, issueData))
                        collected += 1
                        if collected % PROGRESS_EVERY == 0:
                            progress.info(
                                "%s/%s: %d of %d listed issues collected",
                                owner,
                                repo,
                                collected,
                                len(numbers),
                            )
                advance(task, collected + skipped + failed, len(numbers))
        finally:
            finish(task, collected + skipped + failed, len(numbers))

        logger.info(
            "Finished specific collection for %s/%s: %d collected, %d skipped, %d failed",
            owner,
            repo,
            collected,
            skipped,
            failed,
        )
        return SpecificResult(projectId, tuple(issueIds), collected, skipped, failed)

    def _fetchListed(self, owner, repo, num):
        try:
            return self.collectIssue(owner, repo, num)
        except requests.exceptions.HTTPError as e:
            logger.warning("Failed to fetch %s/%s#%d: %s", owner, repo, num, e)
            return None

    def collect(
        self, owner, repo, *, count=None, start=None, end=None, order="desc", closed=False
    ) -> CollectResult:
        self._require_token()
        db = self.store
        max_issues, start_date, end_date, direction = count, start, end, order
        logger.info("Starting collection for %s/%s", owner, repo)
        projectId = None
        collected = 0
        skipped = 0
        pg = 1

        if direction not in ("desc", "asc"):
            direction = "desc"

        task = f"Collecting {owner}/{repo}"
        state = CLOSED_STATE if closed else "all"
        try:
            while True:
                issuesUrl = f"https://api.github.com/repos/{owner}/{repo}/issues?state={state}&sort=created&direction={direction}"
                try:
                    issues = self.get(f"{issuesUrl}&per_page={PER_PAGE}&page={pg}").json()
                except requests.RequestException as error:
                    raise self._github_failure(error, owner, repo) from error
                if projectId is None:
                    projectId = db.save_project(owner, repo)

                for issue in issues:
                    if issue.get("pull_request"):
                        continue

                    created = issue.get("created_at")

                    if direction == "desc":
                        if end_date and created and created > end_date:
                            continue
                        if start_date and created and created < start_date:
                            logger.info("Reached start_date boundary, stopping.")
                            return self._finish(owner, repo, collected, skipped, closed)
                    else:
                        if start_date and created and created < start_date:
                            continue
                        if end_date and created and created > end_date:
                            logger.info("Reached end_date boundary, stopping.")
                            return self._finish(owner, repo, collected, skipped, closed)

                    num = issue.get("number")
                    if db.issue_id(projectId, num):
                        skipped += 1
                        continue

                    try:
                        issueData = self.collectIssue(owner, repo, num)
                    except requests.RequestException as error:
                        if not self._transient(error):
                            raise
                        failure = self._github_failure(error, owner, repo)
                        raise InexError(
                            failure.code,
                            f"{failure.message} {collected} issues collected so far are saved; "
                            "run the collection again to continue.",
                            collected=collected,
                        ) from error
                    if issueData is None:
                        continue

                    db.save_issue(projectId, issueData)
                    collected += 1
                    advance(task, collected, max_issues)

                    if collected % PROGRESS_EVERY == 0:
                        target = f" of {max_issues}" if max_issues else ""
                        progress.info(
                            "%s/%s: %d%s issues collected, %d already saved",
                            owner,
                            repo,
                            collected,
                            target,
                            skipped,
                        )

                    if max_issues and collected >= max_issues:
                        logger.info("Reached max_issues cap.")
                        return self._finish(owner, repo, collected, skipped, closed)

                if len(issues) < PER_PAGE:
                    break
                pg += 1

            return self._finish(owner, repo, collected, skipped, closed)
        finally:
            finish(task, collected, max_issues)

    def _finish(self, owner, repo, collected, skipped, closed=False):
        logger.info(
            "Finished collection for %s/%s: %d collected, %d skipped",
            owner,
            repo,
            collected,
            skipped,
        )
        return CollectResult(collected, skipped, self._stored(owner, repo, closed))
