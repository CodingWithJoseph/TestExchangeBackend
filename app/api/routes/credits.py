from fastapi import APIRouter

from app.api.deps import DBSession
from app.core.auth import AuthenticatedUser
from app.models import CreditLedgerEntry
from app.schemas.api import CreditBalanceRead, CreditLedgerEntryRead
from app.services.common import get_profile
from app.services.credits import ensure_credit_account, list_credit_entries

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("/balance", response_model=CreditBalanceRead)
def balance(user: AuthenticatedUser, db: DBSession) -> CreditBalanceRead:
    get_profile(db, user.id)
    account = ensure_credit_account(db, user.id)
    return CreditBalanceRead(user_id=user.id, balance=account.balance)


@router.get("/ledger", response_model=list[CreditLedgerEntryRead])
def ledger(user: AuthenticatedUser, db: DBSession) -> list[CreditLedgerEntry]:
    get_profile(db, user.id)
    return list_credit_entries(db, user.id)
