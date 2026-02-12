from __future__ import annotations

import telebot

from application.services import (
    ExternalContext,
    buy_chips_from_bank,
    buy_chips_from_user,
    logout_external_identity,
    register_or_login_user,
    sell_chips_to_bank,
    sell_chips_to_user,
)
from domain.repositories import AccountRepository, IdentityRepository, UserRepository
from infrastructure.db.table_repository_sqlite import SqliteTableRepository


def _build_external_context(message) -> ExternalContext:
    """Extract a channel-agnostic context object from a Telegram message."""

    return ExternalContext(
        provider="telegram",
        provider_user_id=str(message.from_user.id),
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
    )


def create_telegram_bot(
    bot_token: str,
    user_repo: UserRepository,
    identity_repo: IdentityRepository,
    account_repo: AccountRepository,
    table_repo: SqliteTableRepository,
) -> telebot.TeleBot:
    """
    Configure and return a TeleBot instance wired to the application layer.

    This module contains only Telegram-specific concerns: parsing Telegram
    messages/callbacks and mapping them to/from application services.
    """

    bot = telebot.TeleBot(bot_token)

    @bot.message_handler(commands=["start", "hello"])
    def handle_start(message):
        bot.send_message(
            message.chat.id,
            "Welcome to the poker table bot!\n"
            "Use /buy and /sell to manage chips.\n"
            "Type /help to see available commands.",
        )

    @bot.message_handler(commands=["help"])
    def handle_help(message):
        bot.send_message(
            message.chat.id,
            "/new <table>           - create a new table\n"
            "/buy <amount> [user]   - buy chips (bank if no user, or from username)\n"
            "/sell <amount> [user]  - sell chips (bank if no user, or to username)\n"
            "/list                  - list all players at the table\n"
            "/join <username>       - register/login with username\n"
            "/leave                 - logout from this device\n",
        )

    @bot.message_handler(commands=["new"])
    def handle_new_table(message):
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Usage: /new <table_name>")
            return

        _, table_name = parts[0], parts[1]
        created = table_repo.create_table(table_name)
        if not created:
            bot.send_message(message.chat.id, f"Table '{table_name}' already exists.")
        else:
            bot.send_message(message.chat.id, f"Table '{table_name}' has been created.")

    @bot.message_handler(commands=["join"])
    def handle_join(message):
        parts = message.text.split()
        if len(parts) < 3:
            bot.send_message(
                message.chat.id,
                "Usage: /join <table> <username>",
            )
            return

        _, table_name, username = parts[0], parts[1], parts[2]

        if not table_repo.exists(table_name):
            bot.send_message(
                message.chat.id,
                f"Table '{table_name}' does not exist. Use /new {table_name} first.",
            )
            return

        external_ctx = _build_external_context(message)

        result = register_or_login_user(
            external_ctx,
            username,
            account_repo,
            identity_repo,
            user_repo,
        )
        if not result.success:
            bot.send_message(message.chat.id, result.error_message)
        else:
            # Link this user to the specific table.
            user = identity_repo.find_user_by_external(
                external_ctx.provider, external_ctx.provider_user_id
            )
            if user is not None:
                table_repo.add_user_to_table(table_name, user.id)

            bot.send_message(
                message.chat.id,
                f"You have joined table '{table_name}' as '{username}'. You can now buy/sell chips.",
            )

    @bot.message_handler(commands=["leave"])
    def handle_leave(message):
        external_ctx = _build_external_context(message)
        logout_external_identity(external_ctx, identity_repo)
        bot.send_message(message.chat.id, "You have been logged out on this device.")

    @bot.message_handler(commands=["list"])
    def handle_list(message):
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Usage: /list <table>")
            return

        _, table_name = parts[0], parts[1]

        if not table_repo.exists(table_name):
            bot.send_message(message.chat.id, f"Table '{table_name}' does not exist.")
            return

        user_ids = table_repo.get_user_ids_for_table(table_name)
        if not user_ids:
            bot.send_message(message.chat.id, f"No players at table '{table_name}'.")
            return

        users = []
        for uid in user_ids:
            u = user_repo.get_user(uid)
            if u is not None:
                users.append(u)

        if not users:
            bot.send_message(message.chat.id, f"No players at table '{table_name}'.")
            return

        lines = [f"{u.first_name}: {u.balance}" for u in users]
        total = sum(u.balance for u in users)
        lines.append(f"Total balance: {total}")

        bot.send_message(message.chat.id, "\n".join(lines))

    @bot.message_handler(commands=["buy", "sell"])
    def handle_transaction(message):
        parts = message.text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Please enter amount of chips.")
            return

        op = parts[0][1:]  # strip leading '/'

        try:
            amount = int(parts[1])
        except ValueError:
            bot.send_message(message.chat.id, "Amount must be a number.")
            return

        username = parts[2] if len(parts) > 2 else None

        external_ctx = _build_external_context(message)

        try:
            if op == "buy":
                if username:
                    # Player-to-player buy using a platform username.
                    result = buy_chips_from_user(
                        external_ctx,
                        amount,
                        username,
                        account_repo,
                        identity_repo,
                        user_repo,
                    )
                else:
                    # Bank buy.
                    result = buy_chips_from_bank(
                        external_ctx, amount, identity_repo, user_repo
                    )
            elif op == "sell":
                if username:
                    result = sell_chips_to_user(
                        external_ctx,
                        amount,
                        username,
                        account_repo,
                        identity_repo,
                        user_repo,
                    )
                else:
                    # Bank sell.
                    result = sell_chips_to_bank(
                        external_ctx, amount, identity_repo, user_repo
                    )
            else:
                bot.send_message(message.chat.id, "Unknown operation.")
                return

            if not result.success:
                bot.send_message(message.chat.id, result.error_message)
                return

            text = (
                result.broadcasts[0].text
                if result.broadcasts
                else "Operation completed."
            )
            bot.send_message(message.chat.id, text)
        except Exception as exc:  # Keep a broad catch to mirror original behavior.
            bot.send_message(message.chat.id, str(exc))

    return bot

