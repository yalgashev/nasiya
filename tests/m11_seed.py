from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_activation.contracts import RegistrationReadinessSnapshot
from app.customer_document.models import CustomerDocument
from app.customer_identity.canonicalization import canonicalize_customer_identity
from app.customer_identity.contracts import IdentityRevision
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityKeyId,
    compute_jshshir_blind_index,
    encrypt_customer_identity,
)
from app.customer_identity.models import CustomerIdentity
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.offers.models import OfferAcceptance, OfferText, OfferVersion
from app.otp.crypto import OtpBrowserBindingDigest
from app.storage.models import ObjectFile, ObjectFileStatus
from app.telegram.models import TelegramLink

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
REGISTRATION_DIGEST = OtpBrowserBindingDigest("a" * 64)


def synthetic_identity_crypto_config() -> CustomerIdentityCryptoConfig:
    key_id = CustomerIdentityKeyId("identity-v1")
    return CustomerIdentityCryptoConfig(
        active_key_id=key_id,
        encryption_keys={key_id: CustomerIdentityAesKey.from_bytes(bytes(range(32)))},
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(
            bytes(reversed(range(32)))
        ),
    )


def seed_registration_snapshot(
    session: Session,
    *,
    phone: str,
) -> RegistrationReadinessSnapshot:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_980_001_321,
        linked_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()

    version = OfferVersion(
        purpose=OfferPurpose.REGISTRATION.value,
        version_number=1,
        status=OfferStatus.CURRENT.value,
        created_by_user_id=user.id,
        created_at=NOW,
        legal_review_authority="Synthetic Legal",
        legal_reviewed_at=NOW,
        legal_review_reference="M11-SYNTHETIC-1",
        approved_by_user_id=user.id,
        approved_at=NOW,
        current_by_user_id=user.id,
        current_at=NOW,
    )
    session.add(version)
    session.flush()
    offer_text = OfferText(
        offer_version_id=version.id,
        language=OfferLanguage.UZ_LATN.value,
        title="Synthetic registration offer",
        body="Synthetic registration offer body",
        content_hash="c" * 64,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(offer_text)
    session.flush()
    acceptance = OfferAcceptance(
        user_id=user.id,
        offer_version_id=version.id,
        offer_text_id=offer_text.id,
        purpose=OfferPurpose.REGISTRATION.value,
        language=OfferLanguage.UZ_LATN.value,
        version_number=1,
        content_hash=offer_text.content_hash,
        accepted_at=NOW,
        user_agent=None,
    )
    session.add(acceptance)
    session.flush()

    crypto_config = synthetic_identity_crypto_config()
    canonical_identity = canonicalize_customer_identity(
        first_name="Synthetic",
        last_name="Specimen",
        middle_name=None,
        jshshir="12345678901234",
        document_type="PASSPORT",
        document_number="AB 12345",
    )
    envelope = encrypt_customer_identity(
        canonical_identity,
        customer_id=customer.id,
        crypto_config=crypto_config,
    )
    identity = CustomerIdentity(
        customer_id=customer.id,
        ciphertext=envelope.ciphertext,
        nonce=envelope.nonce,
        key_id=envelope.key_id.as_persistence_value(),
        schema_version=envelope.schema_version,
        jshshir_blind_index=compute_jshshir_blind_index(
            canonical_identity.jshshir,
            blind_index_key=crypto_config.get_blind_index_key(),
        ).as_persistence_bytes(),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(identity)
    object_id = uuid4()
    object_file = ObjectFile(
        id=object_id,
        bucket="nasiya-private-test",
        object_key=f"v1/objects/{object_id.hex}.png",
        content_type="image/png",
        size_bytes=128,
        checksum_sha256="d" * 64,
        width_px=8,
        height_px=6,
        status=ObjectFileStatus.AVAILABLE.value,
        created_by_user_id=user.id,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
        available_at=NOW,
        terminal_at=None,
        deleted_at=None,
    )
    session.add(object_file)
    session.flush()
    document = CustomerDocument(
        customer_id=customer.id,
        object_file_id=object_file.id,
        submission_id=uuid4(),
        status="CURRENT",
        attached_by_user_id=user.id,
        attached_at=NOW,
        superseded_by_document_id=None,
        superseded_at=None,
    )
    session.add(document)
    session.flush()
    return RegistrationReadinessSnapshot(
        user_id=user.id,
        customer_id=customer.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        registration_offer_acceptance_id=acceptance.id,
        customer_identity_revision=IdentityRevision(identity.revision),
        customer_document_id=document.id,
        browser_binding_digest=REGISTRATION_DIGEST,
    )
