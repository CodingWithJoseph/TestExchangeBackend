from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Dispute, EvidenceItem, EvidenceSubmission
from app.services.common import DomainError, add_audit_event
from app.services.workflow import _validate_storage_key


def sign_storage_evidence(settings: Settings, storage_key: str) -> str:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise DomainError("Moderator evidence access is not configured. Contact support.", 503)
    base = settings.supabase_url.rstrip("/")
    secret = settings.supabase_service_role_key.get_secret_value()
    path = "/object/sign/test-evidence/" + quote(storage_key, safe="/")
    try:
        response = httpx.post(
            base + "/storage/v1" + path,
            headers={"apikey": secret, "Authorization": f"Bearer {secret}"},
            json={"expiresIn": 60},
            timeout=10,
            follow_redirects=False,
        )
        response.raise_for_status()
        signed_path = response.json()["signedURL"]
        if not isinstance(signed_path, str) or not signed_path.startswith(path + "?token="):
            raise ValueError("Unexpected signed path")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Do not include provider responses, credentials or signed tokens in API errors.
        raise DomainError("Unable to open this attachment. Please try again.", 502) from None
    return base + "/storage/v1" + signed_path


def moderator_evidence_url(
    db: Session, *, dispute_id: UUID, evidence_id: UUID, moderator_id: UUID, settings: Settings
) -> str:
    dispute = db.get(Dispute, dispute_id)
    item = db.get(EvidenceItem, evidence_id)
    submission = db.get(EvidenceSubmission, item.submission_id) if item else None
    if (
        dispute is None
        or item is None
        or submission is None
        or submission.assignment_id != dispute.assignment_id
        or not item.storage_key
    ):
        raise DomainError("Attachment not found in this moderation case", 404)
    _validate_storage_key(dispute.assignment_id, item.storage_key)
    url = sign_storage_evidence(settings, item.storage_key)
    add_audit_event(
        db,
        actor_id=moderator_id,
        action="moderation.evidence_opened",
        entity_type="assignment",
        entity_id=dispute.assignment_id,
        details={"dispute_id": str(dispute_id), "evidence_id": str(evidence_id)},
    )
    db.flush()
    return url
