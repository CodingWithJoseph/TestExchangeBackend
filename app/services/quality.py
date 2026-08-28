import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContractTask, TestingContract
from app.models.enums import SubmissionStatus
from app.schemas.api import QualityCheckItem, QualityCheckRead
from app.services.common import DomainError, get_assignment
from app.services.workflow import get_submission

SUMMARY_MIN_CHARS = 80
OBSERVATION_MIN_CHARS = 30
GENERIC_PHRASES = (
    "works fine",
    "looks good",
    "no issues",
    "everything works",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _check(
    *, code: str, label: str, passed: bool, passed_detail: str, flagged_detail: str
) -> QualityCheckItem:
    return QualityCheckItem(
        code=code,
        label=label,
        status="passed" if passed else "flagged",
        detail=passed_detail if passed else flagged_detail,
    )


def build_submission_quality_check(
    db: Session, *, submission_id: UUID, user_id: UUID
) -> QualityCheckRead:
    """Build a private, repeatable quality signal for a submission.

    This function intentionally does not decide a review outcome or write credits. It only
    explains whether a submission gives a human reviewer enough structured information to make
    that decision.
    """
    submission, items = get_submission(db, submission_id=submission_id, user_id=user_id)
    assignment = get_assignment(db, submission.assignment_id)
    contract_id = db.scalar(
        select(TestingContract.id).where(TestingContract.campaign_id == assignment.campaign_id)
    )
    if contract_id is None:
        # This should be impossible for a valid submission, but keeps the service explicit if
        # old data was created before contracts were required.
        raise DomainError("The campaign does not have a testing contract", 409)
    tasks = list(
        db.scalars(
            select(ContractTask)
            .where(ContractTask.contract_id == contract_id)
            .order_by(ContractTask.position, ContractTask.id)
        )
    )

    checks: list[QualityCheckItem] = []
    covered_task_ids = {item.task_id for item in items if item.task_id is not None}
    required_task_ids = {task.id for task in tasks if task.evidence_required}
    coverage_complete = required_task_ids.issubset(covered_task_ids)
    checks.append(
        _check(
            code="required_task_coverage",
            label="Required tasks have linked evidence",
            passed=coverage_complete,
            passed_detail=f"All {len(required_task_ids)} required task(s) have linked evidence.",
            flagged_detail=(
                f"{len(required_task_ids - covered_task_ids)} required task(s) still need "
                "linked evidence."
            ),
        )
    )

    content_complete = all(
        item.storage_key is not None or item.external_url is not None or item.note is not None
        for item in items
    )
    checks.append(
        _check(
            code="evidence_content",
            label="Every evidence item includes content",
            passed=content_complete,
            passed_detail=f"All {len(items)} evidence item(s) include a file, link, or note.",
            flagged_detail="One or more evidence items do not contain reviewable content.",
        )
    )

    normalized_summary = _normalize_text(submission.summary)
    summary_specific = len(normalized_summary) >= SUMMARY_MIN_CHARS and not any(
        phrase in normalized_summary for phrase in GENERIC_PHRASES
    )
    checks.append(
        _check(
            code="summary_specificity",
            label="Summary describes a concrete result",
            passed=summary_specific,
            passed_detail=(
                f"The summary contains {len(submission.summary.strip())} characters of "
                "specific context."
            ),
            flagged_detail=(
                f"Add a concrete result to the summary (at least {SUMMARY_MIN_CHARS} "
                "characters; avoid generic phrases)."
            ),
        )
    )

    observation_lengths = [len((item.note or "").strip()) for item in items]
    has_observation = any(length >= OBSERVATION_MIN_CHARS for length in observation_lengths)
    checks.append(
        _check(
            code="concrete_observation",
            label="Evidence includes a reproducible observation",
            passed=has_observation,
            passed_detail="At least one evidence note records a concrete observation.",
            flagged_detail=(
                f"Add an observation of at least {OBSERVATION_MIN_CHARS} characters, "
                "including what happened and where."
            ),
        )
    )

    score = round(sum(item.status == "passed" for item in checks) / len(checks) * 100)
    if submission.status != SubmissionStatus.SUBMITTED:
        overall_status = "already_reviewed"
    elif any(item.status == "flagged" for item in checks):
        overall_status = "needs_attention"
    else:
        overall_status = "ready_for_review"

    return QualityCheckRead(
        submission_id=submission.id,
        assignment_id=assignment.id,
        submission_version=submission.version,
        submission_status=submission.status,
        status=overall_status,
        score=score,
        checks=checks,
        disclaimer=(
            "Advisory only: this check never approves, rejects, or transfers credits. "
            "The campaign owner or a moderator makes the final decision."
        ),
    )
