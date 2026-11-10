from conftest import load_inex_profile

from inex.services.classification import ClassificationService, DatasetSummary, ProjectSummary

RUN = ("qwen3:30b", "inex@test+t2", 0.2)
OTHER_RUN = ("m2", "inex@test+t2", 0.2)
AT = "2026-11-10T00:00:00"


def label(db, issue_id, classification, run=RUN, status="ok"):
    data = {"classification": classification, "status": status}
    if status != "ok":
        data["error"] = "boom"
    db.save_classification(issue_id, data, *run, AT)


def link(db, pid, issue_id):
    db.save_version(pid, "widget", "1.0.0", "2023-01-01T00:00:00Z", {}, None)
    db.link_issue_to_version(issue_id, db.version_id(pid, "1.0.0"))


def test_counts_are_per_project_and_label_for_one_run(seeded):
    db, pid, ids = seeded
    db.save_project("acme", "empty")
    label(db, ids[0], "Intrinsic")
    label(db, ids[1], "Not a Bug")
    label(db, ids[2], None, status="api_error")
    label(db, ids[0], "Extrinsic", run=OTHER_RUN)
    link(db, pid, ids[0])
    projects, labels = db.summary_counts(*RUN)
    assert projects == [("acme", "empty", 0, 0), ("acme", "widget", 3, 1)]
    assert sorted(labels) == [
        ("acme", "widget", "Intrinsic", 1),
        ("acme", "widget", "Not a Bug", 1),
    ]


def test_the_summary_uses_the_current_run_and_the_profile_label_order(seeded):
    db, pid, ids = seeded
    service = ClassificationService(db, load_inex_profile(), "qwen3:30b", 0.2)
    model, prompt_version, temperature = service._runKey()
    run = (model, prompt_version, temperature)
    label(db, ids[0], "Not a Bug", run=run)
    label(db, ids[1], "Intrinsic", run=run)
    label(db, ids[2], "Intrinsic", run=OTHER_RUN)
    link(db, pid, ids[0])
    assert service.summary() == DatasetSummary(
        model=model,
        prompt_version=prompt_version,
        temperature=temperature,
        labels=("Intrinsic", "Extrinsic", "Not a Bug", "Unknown"),
        projects=(
            ProjectSummary(
                owner="acme",
                repo="widget",
                issues=3,
                labelled=2,
                linked=1,
                labels={"Intrinsic": 1, "Extrinsic": 0, "Not a Bug": 1, "Unknown": 0},
            ),
        ),
    )


def test_an_empty_database_has_no_projects(db):
    service = ClassificationService(db, load_inex_profile(), "qwen3:30b", 0.2)
    assert service.summary().projects == ()
