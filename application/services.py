from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import bcrypt
from uuid import uuid4

from domain.models import Account, User
from domain.repositories import AccountRepository, IdentityRepository, UserRepository


@dataclass
class ExternalContext:
    """
    Information about the caller from a particular channel (Telegram, Discord, web).

    The application layer never depends on concrete SDK types; it only sees
    this small context object.
    """

    provider: str
    provider_user_id: str
    first_name: str
    last_name: str


@dataclass
class BroadcastMessage:
    """A message that should be delivered to a particular user."""

    user_id: str
    text: str


@dataclass
class OperationResult:
    """Generic result type for simple operations."""

    success: bool
    error_message: Optional[str] = None
    broadcasts: List[BroadcastMessage] = field(default_factory=list)


@dataclass
class InitiateBuyFromResult:
    """Result of initiating a player-to-player buy request."""

    success: bool
    error_message: Optional[str] = None
    source_user: Optional[User] = None
    candidates: List[User] = field(default_factory=list)


def _validate_positive_amount(amount: int) -> Optional[str]:
    if amount <= 0:
        return "Amount must be greater than zero."
    return None


def _get_logged_in_user(
    external_ctx: ExternalContext,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> Optional[User]:
    """
    Resolve the currently logged-in user for an external identity.

    Returns None if the external identity is not associated with any
    account/user (i.e. the caller must /join first).
    """

    return identity_repo.find_user_by_external(
        external_ctx.provider,
        external_ctx.provider_user_id,
    )


def register_or_login_user(
    external_ctx: ExternalContext,
    username: str,
    account_repo: AccountRepository,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Register a new platform account or log in to an existing one, then
    associate the external identity with that account.
    """

    existing = account_repo.get_by_username(username)
    if existing is None:
        # Registration path. Passwords are no longer used; we store
        # an empty hash placeholder for backwards compatibility.
        account_id = uuid4().hex
        account = Account(id=account_id, username=username, password_hash="")
        account_repo.create_account(account)

        # Create a corresponding User with zero balance.
        # We no longer care about first/last name, only username.
        user_repo.add_user(
            User(
                id=account_id,
                first_name=username,
                last_name="",
                balance=0,
            )
        )
    else:
        # Login path without password: just reuse the existing account.
        account_id = existing.id

    # Link this external identity to the account.
    identity_repo.set_external_identity(
        external_ctx.provider,
        external_ctx.provider_user_id,
        account_id,
    )

    return OperationResult(success=True)


def logout_external_identity(
    external_ctx: ExternalContext,
    identity_repo: IdentityRepository,
) -> OperationResult:
    """
    Remove any association between this external identity and a user
    account. The account and its balance remain; the user simply needs
    to /join again to play.
    """

    identity_repo.clear_external_identity(
        external_ctx.provider,
        external_ctx.provider_user_id,
    )
    return OperationResult(success=True)


def buy_chips_from_bank(
    external_ctx: ExternalContext,
    amount: int,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Handle buying chips from the bank.

    Semantics are kept close to the original implementation:
    - The caller's balance is decreased by `amount`.
    - All users are notified of the transaction.
    """

    error = _validate_positive_amount(amount)
    if error:
        return OperationResult(success=False, error_message=error)

    user = _get_logged_in_user(external_ctx, identity_repo, user_repo)
    if user is None:
        return OperationResult(
            success=False,
            error_message="You must /join <username> <password> before buying chips.",
        )

    user_repo.update_balance(user.id, -amount)

    # Message uses the platform username (stored in `first_name`).
    text = f"{user.first_name} buys {amount}"
    broadcasts = [BroadcastMessage(user_id=user.id, text=text)]

    return OperationResult(success=True, broadcasts=broadcasts)


def sell_chips_to_bank(
    external_ctx: ExternalContext,
    amount: int,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Handle selling chips to the bank.

    - The caller's balance is increased by `amount`.
    - All users are notified of the transaction.
    """

    error = _validate_positive_amount(amount)
    if error:
        return OperationResult(success=False, error_message=error)

    user = _get_logged_in_user(external_ctx, identity_repo, user_repo)
    if user is None:
        return OperationResult(
            success=False,
            error_message="You must /join <username> <password> before selling chips.",
        )

    user_repo.update_balance(user.id, amount)

    text = f"{user.first_name} sells {amount}"
    broadcasts = [BroadcastMessage(user_id=user.id, text=text)]

    return OperationResult(success=True, broadcasts=broadcasts)


def buy_chips_from_user(
    external_ctx: ExternalContext,
    amount: int,
    counterparty_username: str,
    account_repo: AccountRepository,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Buy chips from another player identified by username.

    - The caller (buyer) must be logged in.
    - The counterparty username must exist.
    - The buyer's balance is decreased by `amount`.
    - The seller's balance is increased by `amount`.
    """

    error = _validate_positive_amount(amount)
    if error:
        return OperationResult(success=False, error_message=error)

    buyer = _get_logged_in_user(external_ctx, identity_repo, user_repo)
    if buyer is None:
        return OperationResult(
            success=False,
            error_message="You must /join <username> <password> before buying from another player.",
        )

    seller_account = account_repo.get_by_username(counterparty_username)
    if seller_account is None:
        return OperationResult(success=False, error_message="Seller username not found.")

    seller = user_repo.get_user(seller_account.id)
    if seller is None:
        return OperationResult(success=False, error_message="Seller user not found.")

    if buyer.id == seller.id:
        return OperationResult(
            success=False,
            error_message="You cannot buy chips from yourself.",
        )

    user_repo.update_balance(buyer.id, -amount)
    user_repo.update_balance(seller.id, amount)

    # Single, neutral message: buyer does something to seller.
    text = f"{buyer.first_name} buys {amount} from {seller.first_name}"
    broadcasts = [
        BroadcastMessage(user_id=buyer.id, text=text),
        BroadcastMessage(user_id=seller.id, text=text),
    ]

    return OperationResult(success=True, broadcasts=broadcasts)


def sell_chips_to_user(
    external_ctx: ExternalContext,
    amount: int,
    counterparty_username: str,
    account_repo: AccountRepository,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Sell chips to another player identified by username.

    - The caller (seller) must be logged in.
    - The counterparty username must exist.
    - The buyer's balance is decreased by `amount`.
    - The seller's balance is increased by `amount`.
    """

    error = _validate_positive_amount(amount)
    if error:
        return OperationResult(success=False, error_message=error)

    seller = _get_logged_in_user(external_ctx, identity_repo, user_repo)
    if seller is None:
        return OperationResult(
            success=False,
            error_message="You must /join <username> <password> before selling to another player.",
        )

    buyer_account = account_repo.get_by_username(counterparty_username)
    if buyer_account is None:
        return OperationResult(success=False, error_message="Buyer username not found.")

    buyer = user_repo.get_user(buyer_account.id)
    if buyer is None:
        return OperationResult(success=False, error_message="Buyer user not found.")

    if buyer.id == seller.id:
        return OperationResult(
            success=False,
            error_message="You cannot sell chips to yourself.",
        )

    user_repo.update_balance(buyer.id, -amount)
    user_repo.update_balance(seller.id, amount)

    text = f"{seller.first_name} sells {amount} to {buyer.first_name}"
    broadcasts = [
        BroadcastMessage(user_id=seller.id, text=text),
        BroadcastMessage(user_id=buyer.id, text=text),
    ]

    return OperationResult(success=True, broadcasts=broadcasts)


def initiate_buy_from_player(
    external_ctx: ExternalContext,
    amount: int,
    identity_repo: IdentityRepository,
    user_repo: UserRepository,
) -> InitiateBuyFromResult:
    """
    Start a player-to-player buy flow:
    - Resolve the source user from the external context.
    - Return the list of potential target users (other players).
    """

    error = _validate_positive_amount(amount)
    if error:
        return InitiateBuyFromResult(success=False, error_message=error)

    source_user = _get_logged_in_user(external_ctx, identity_repo, user_repo)
    if source_user is None:
        return InitiateBuyFromResult(
            success=False,
            error_message="You must /join <username> <password> before buying from another player.",
        )

    all_users = user_repo.get_all_users()
    candidates = [u for u in all_users if u.id != source_user.id]

    if not candidates:
        return InitiateBuyFromResult(
            success=False,
            error_message="No other players available to buy from.",
            source_user=source_user,
            candidates=[],
        )

    return InitiateBuyFromResult(
        success=True,
        source_user=source_user,
        candidates=candidates,
    )


def confirm_buy_from_player(
    source_user_id: str,
    target_user_id: str,
    amount: int,
    user_repo: UserRepository,
) -> OperationResult:
    """
    Confirm a player-to-player buy:
    - Debit the source user's balance.
    - Credit the target user's balance.
    - Broadcast the transaction to all users.
    """

    error = _validate_positive_amount(amount)
    if error:
        return OperationResult(success=False, error_message=error)

    source = user_repo.get_user(source_user_id)
    target = user_repo.get_user(target_user_id)

    if source is None or target is None:
        return OperationResult(success=False, error_message=" Buyer or seller not found.")

    user_repo.update_balance(source.id, -amount)
    user_repo.update_balance(target.id, amount)

    users = user_repo.get_all_users()
    text = (
        f"{source.first_name} buys {amount} "
        f"from {target.first_name}"
    )
    broadcasts = [BroadcastMessage(user_id=u.id, text=text) for u in users]

    return OperationResult(success=True, broadcasts=broadcasts)


def reject_buy_from_player(
    source_user_id: str,
    target_user_id: str,  # kept for symmetry / potential future use
    amount: int,  # kept for symmetry / potential logging
) -> OperationResult:
    """
    Handle the case where the seller declines the buy request.

    Currently we only notify the source player, mirroring the existing behavior.
    """

    # The caller (interface layer) is responsible for ensuring `source_user_id`
    # is a meaningful external/chat ID for the current channel.
    text = "haha sorry"
    broadcasts = [BroadcastMessage(user_id=source_user_id, text=text)]

    return OperationResult(success=True, broadcasts=broadcasts)

