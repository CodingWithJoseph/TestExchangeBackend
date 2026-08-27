from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreditAccount, CreditLedgerEntry
from app.models.enums import CreditEntryType
from app.services.common import DomainError


def ensure_credit_account(db: Session, user_id: UUID) -> CreditAccount:
    account = db.get(CreditAccount, user_id)
    if account is None:
        account = CreditAccount(user_id=user_id, balance=0)
        db.add(account)
        db.flush()
    return account


def get_credit_account_for_update(db: Session, user_id: UUID) -> CreditAccount:
    account = db.scalar(
        select(CreditAccount).where(CreditAccount.user_id == user_id).with_for_update()
    )
    if account is None:
        return ensure_credit_account(db, user_id)
    return account


def record_credit_entry(
    db: Session,
    *,
    user_id: UUID,
    delta: int,
    entry_type: CreditEntryType,
    idempotency_key: str,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    note: str | None = None,
    created_by: UUID | None = None,
    transaction_id: UUID | None = None,
) -> CreditLedgerEntry:
    existing = db.scalar(
        select(CreditLedgerEntry).where(CreditLedgerEntry.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    if delta == 0:
        raise DomainError("Credit changes cannot be zero")

    account = get_credit_account_for_update(db, user_id)
    resulting_balance = account.balance + delta
    if resulting_balance < 0:
        raise DomainError("Insufficient credits", 409)

    account.balance = resulting_balance
    entry = CreditLedgerEntry(
        transaction_id=transaction_id or uuid4(),
        user_id=user_id,
        delta=delta,
        entry_type=entry_type,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(entry)
    db.flush()
    return entry


def list_credit_entries(db: Session, user_id: UUID) -> list[CreditLedgerEntry]:
    return list(
        db.scalars(
            select(CreditLedgerEntry)
            .where(CreditLedgerEntry.user_id == user_id)
            .order_by(CreditLedgerEntry.created_at.desc(), CreditLedgerEntry.id.desc())
        )
    )
