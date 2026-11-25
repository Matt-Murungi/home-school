"""Chart of accounts (SR 7.10). Codes are fixed and never reused; ids derive from codes, so
creation is an atomic claim and concurrent `ensure` calls converge on one row.

Refunds and credits is a CONTRA-REVENUE account: type `revenue`, normal side `debit` (the M04 enum
has no contra type; the live project calls it `contra_revenue`, owner Q17)."""

from __future__ import annotations

from .errors import InvalidTransaction
from .model import ID_CHARS, Account, AccountType, Side

RECEIVABLES = "receivable:parents"
CASH_MTN = "cash:mtn_momo"
CASH_AIRTEL = "cash:airtel_money"
CASH_CARD = "cash:card_gateway"
REVENUE = "revenue:platform"
REFUNDS_CREDITS = "contra_revenue:refunds_credits"
PROVIDER_FEES = "expense:provider_fees"
TEACHER_PAYABLE_PREFIX = "teacher_payable:"

# code -> (name, type, normal side)
FIXED_CHART: dict[str, tuple[str, AccountType, Side]] = {
    RECEIVABLES: ("Parent receivables", AccountType.ASSET, Side.DEBIT),
    CASH_MTN: ("Cash at MTN MoMo", AccountType.ASSET, Side.DEBIT),
    CASH_AIRTEL: ("Cash at Airtel Money", AccountType.ASSET, Side.DEBIT),
    CASH_CARD: ("Cash at card gateway", AccountType.ASSET, Side.DEBIT),
    REVENUE: ("Platform revenue", AccountType.REVENUE, Side.CREDIT),
    REFUNDS_CREDITS: ("Refunds and credits (contra revenue)", AccountType.REVENUE, Side.DEBIT),
    PROVIDER_FEES: ("Provider fees", AccountType.EXPENSE, Side.DEBIT),
}


def teacher_payable_code(teacher_id: str) -> str:
    if not isinstance(teacher_id, str) or not ID_CHARS.fullmatch(teacher_id):
        raise InvalidTransaction("teacher id must be 1-36 characters of [A-Za-z0-9._-]")
    return f"{TEACHER_PAYABLE_PREFIX}{teacher_id}"


def fixed_accounts(created_at: str) -> list[Account]:
    return [
        Account(code=c, name=n, type=t, normal_side=s, created_at=created_at)
        for c, (n, t, s) in FIXED_CHART.items()
    ]


def teacher_payable(teacher_id: str, created_at: str) -> Account:
    return Account(
        code=teacher_payable_code(teacher_id),
        name="Teacher payable",
        type=AccountType.LIABILITY,
        normal_side=Side.CREDIT,
        teacher_id=teacher_id,
        created_at=created_at,
    )
