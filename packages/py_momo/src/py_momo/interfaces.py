from __future__ import annotations

from typing import Protocol

from .status import PaymentResult


class VerifiedCallback(Protocol):
    raw_body: bytes
    result: PaymentResult


class CollectionsClient(Protocol):
    def request_to_pay(
        self,
        *,
        reference_id: str,
        amount_ugx: int,
        payer_msisdn: str,
        external_id: str,
        payer_message: str = "",
        payee_note: str = "",
    ) -> PaymentResult: ...

    def get_status(self, reference_id: str) -> PaymentResult: ...

    def verify_callback(
        self,
        raw_body: bytes,
        *,
        reference_id: str | None = None,
        expected_amount_ugx: int | None = None,
    ) -> VerifiedCallback: ...


class DisbursementsClient(Protocol):
    def transfer(
        self,
        *,
        reference_id: str,
        amount_ugx: int,
        payee_msisdn: str,
        external_id: str,
        payer_message: str = "",
        payee_note: str = "",
    ) -> PaymentResult: ...

    def get_status(self, reference_id: str) -> PaymentResult: ...

    def verify_callback(
        self,
        raw_body: bytes,
        *,
        reference_id: str | None = None,
        expected_amount_ugx: int | None = None,
    ) -> VerifiedCallback: ...
