import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from inex.errors import InexError

logger = logging.getLogger(__name__)

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
MAJOR_RANGE_RE = re.compile(r"^(\d+)\.x$")
SPAN_RANGE_RE = re.compile(r"^(\d+\.\d+\.\d+)\.\.(\d+\.\d+\.\d+)$")
MAJOR_RE = re.compile(r"^(\d+)\.")
EMPTY_FILTERS = ("", "*", "all", "none")


@dataclass(frozen=True)
class LinkResult:
    package_name: str
    issue_versions_snapshotted: int
    slice_versions_snapshotted: int
    linked_issues: int
    already_linked: int
    unlinkable_pre_release: int


@cache
def parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_prerelease(version):
    return "-" in str(version)


def sort_versions_desc(versions):
    def key(version):
        match = SEMVER_RE.match(version)
        if not match:
            return (0, 0, 0, 1, version)
        major, minor, patch = map(int, match.groups())
        return (major, minor, patch, 1 if is_prerelease(version) else 0)

    return sorted(versions, key=key, reverse=True)


def live_version_at(issue_created_at, versions_with_times):
    issued = parse_timestamp(issue_created_at)
    if issued is None:
        return None
    best_stable = None
    best_pre = None
    for version, published_text in versions_with_times:
        published = parse_timestamp(published_text)
        if published is None or published > issued:
            continue
        candidate = (published, version, published_text)
        if is_prerelease(version):
            if best_pre is None or published > best_pre[0]:
                best_pre = candidate
        elif best_stable is None or published > best_stable[0]:
            best_stable = candidate
    picked = best_stable or best_pre
    return (picked[1], picked[2]) if picked else None


def filter_versions_by_range(versions, version_range):
    match = MAJOR_RANGE_RE.match(version_range)
    if match:
        major = int(match.group(1))
        return [v for v in versions if (m := MAJOR_RE.match(v)) and int(m.group(1)) == major]
    match = SPAN_RANGE_RE.match(version_range)
    if match:
        low = tuple(int(x) for x in match.group(1).split("."))
        high = tuple(int(x) for x in match.group(2).split("."))
        selected = []
        for version in versions:
            semver = SEMVER_RE.match(version)
            if semver and low <= tuple(int(x) for x in semver.groups()) < high:
                selected.append(version)
        return selected
    logger.warning("Unrecognized version_range %r; ignoring filter", version_range)
    return versions


def select_versions(
    versions_desc, time_map, last_n_versions=None, version_start=None, version_end=None
):
    selected = set()
    if last_n_versions:
        try:
            count = int(last_n_versions)
        except (TypeError, ValueError):
            logger.warning("Bad last_n_versions %r; ignoring", last_n_versions)
        else:
            if count > 0:
                selected.update(versions_desc[:count])
    if version_start or version_end:
        lower, upper = parse_timestamp(version_start), parse_timestamp(version_end)
        for version in versions_desc:
            published = parse_timestamp(time_map.get(version))
            if published is None:
                continue
            if lower and published < lower:
                continue
            if upper and published > upper:
                continue
            selected.add(version)
    return selected


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in EMPTY_FILTERS else text


class LinkageService:
    def __init__(self, store, npm):
        self.store = store
        self.npm = npm

    def _resolve_package_name(self, project_id, npm_package):
        cached = self.store.package_name(project_id)
        if cached and cached == npm_package:
            return cached
        document = self.npm.package_document(npm_package)
        if not (document and document.get("versions")):
            return None
        self.store.set_package_name(project_id, npm_package)
        return npm_package

    def _snapshot_version(self, project_id, package_name, version, published_at, versions_map):
        manifest = versions_map.get(version, {})
        direct = manifest.get("dependencies") or {}
        peer = manifest.get("peerDependencies") or {}
        dev = manifest.get("devDependencies") or {}
        logger.info(
            "  Snapshotting %s@%s (direct=%d, peer=%d, dev=%d)",
            package_name,
            version,
            len(direct),
            len(peer),
            len(dev),
        )
        version_id = self.store.save_version(
            project_id,
            package_name,
            version,
            published_at,
            {"direct": len(direct), "peer": len(peer), "dev": len(dev)},
            {"dependencies": direct, "peerDependencies": peer, "devDependencies": dev},
        )
        rows = [
            (name, kind, rng, None, 0, None)
            for kind, deps in (("direct", direct), ("peer", peer), ("dev", dev))
            for name, rng in deps.items()
        ]
        self.store.save_dependencies(version_id, rows)
        return version_id

    def link(
        self,
        owner,
        repo,
        package,
        *,
        start=None,
        end=None,
        version_range=None,
        last_n_versions=None,
        version_start=None,
        version_end=None,
    ) -> LinkResult:
        start, end = _clean(start), _clean(end)
        version_range = _clean(version_range)
        version_start, version_end = _clean(version_start), _clean(version_end)

        project_id = self.store.project_id(owner, repo)
        if project_id is None:
            raise InexError(
                "project_not_found",
                f"{owner}/{repo} is not in this database. Collect it first.",
                owner=owner,
                repo=repo,
            )
        if not self.store.project_has_classifications(project_id):
            raise InexError(
                "no_classifications",
                f"{owner}/{repo} has no classified issues yet. Classify it first.",
                owner=owner,
                repo=repo,
            )
        package_name = self._resolve_package_name(project_id, package)
        if not package_name:
            raise InexError(
                "npm_package_not_found",
                f"The npm registry has no package named {package!r}.",
                package=package,
            )
        document = self.npm.package_document(package_name)
        if not document:
            raise InexError(
                "npm_doc_fetch_failed",
                f"Could not fetch the npm registry document for {package_name!r}.",
                package=package_name,
            )

        versions_map = document.get("versions", {})
        time_map = document.get("time", {})
        all_versions = list(versions_map)
        if version_range:
            all_versions = filter_versions_by_range(all_versions, version_range)
            if not all_versions:
                raise InexError(
                    "no_versions_match_range",
                    f"No {package_name} releases match the range {version_range!r}.",
                    package=package_name,
                    version_range=version_range,
                )
        versions_desc = sort_versions_desc(all_versions)
        versions_with_times = [(v, time_map.get(v)) for v in versions_desc]

        issues = self.store.classified_issue_window(project_id, start, end)
        if not issues:
            raise InexError(
                "no_classified_issues",
                f"{owner}/{repo} has no classified issues in the given window.",
                owner=owner,
                repo=repo,
            )
        slice_versions = select_versions(
            versions_desc, time_map, last_n_versions, version_start, version_end
        )

        snapshotted = linked = unlinkable = already_linked = 0
        for issue_id, issue_number, created_at, existing_version_id in issues:
            if existing_version_id is not None:
                already_linked += 1
                continue
            picked = live_version_at(created_at, versions_with_times)
            if not picked:
                unlinkable += 1
                logger.info(
                    "  Issue #%s created %s predates first release", issue_number, created_at
                )
                continue
            version, published_at = picked
            version_id = self.store.version_id(project_id, version)
            if version_id is None:
                version_id = self._snapshot_version(
                    project_id, package_name, version, published_at, versions_map
                )
                snapshotted += 1
            self.store.link_issue_to_version(issue_id, version_id)
            linked += 1

        extra = 0
        for version in slice_versions:
            if self.store.version_id(project_id, version) is None:
                self._snapshot_version(
                    project_id, package_name, version, time_map.get(version), versions_map
                )
                extra += 1

        return LinkResult(
            package_name=package_name,
            issue_versions_snapshotted=snapshotted,
            slice_versions_snapshotted=extra,
            linked_issues=linked,
            already_linked=already_linked,
            unlinkable_pre_release=unlinkable,
        )
