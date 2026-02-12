from __future__ import annotations

from typing import Dict, Tuple

import discord
from discord.ext import commands

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


def _build_external_context(user: discord.abc.User) -> ExternalContext:
    """Create an `ExternalContext` from a Discord user."""

    # Discord has `name` and `display_name`; here we just store the full
    # display name in `first_name` to keep things simple.
    display_name = user.display_name or user.name
    return ExternalContext(
        provider="discord",
        provider_user_id=str(user.id),
        first_name=display_name,
        last_name="",
    )


def create_discord_bot(
    user_repo: UserRepository,
    identity_repo: IdentityRepository,
    account_repo: AccountRepository,
    table_repo: SqliteTableRepository,
) -> commands.Bot:
    """
    Configure and return a Discord bot with behaviour analogous to
    the Telegram interface: /start, /help, buy/sell chips, and list players.
    """

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    intents.reactions = True

    # Disable the default help command so we can provide our own `!help`.
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    # In-memory store of pending player-to-player buy requests keyed by the
    # confirmation message ID.
    pending_requests: Dict[int, Tuple[str, str, int, int]] = {}
    # value: (buyer_internal_id, seller_internal_id, amount, seller_discord_id)
    @bot.event
    async def on_ready():
        print(f"Discord bot logged in as {bot.user} (id={bot.user.id})")

    @bot.command(name="start")
    async def start_cmd(ctx: commands.Context):
        await ctx.send(
            "Welcome to the poker table bot (Discord)!\n"
            "Use !buy and !sell to manage chips.\n"
            "Type !help to see available commands."
        )

    @bot.command(name="help")
    async def help_cmd(ctx: commands.Context):
        await ctx.send(
            "!new <table>               - create a new table\n"
            "!buy <amount> [user]       - buy chips (bank if no user, or from username)\n"
            "!sell <amount> [user]      - sell chips (bank if no user, or to username)\n"
            "!list <table>              - list all players at the table\n"
            "!join <table> <username>   - register/login and join table\n"
            "!leave                     - logout from this account\n"
        )

    @bot.command(name="new")
    async def new_table_cmd(ctx: commands.Context, table_name: str):
        created = table_repo.create_table(table_name)
        if not created:
            await ctx.send(f"Table '{table_name}' already exists.")
        else:
            await ctx.send(f"Table '{table_name}' has been created.")

    @bot.command(name="join")
    async def join_cmd(ctx: commands.Context, table_name: str, username: str):
        external_ctx = _build_external_context(ctx.author)
        result = register_or_login_user(
            external_ctx,
            username,
            account_repo,
            identity_repo,
            user_repo,
        )
        if not result.success:
            await ctx.send(result.error_message or "Join failed.")
        else:
            # Link this user to the specific table.
            user = identity_repo.find_user_by_external(
                "discord", str(ctx.author.id)
            )
            if user is not None:
                table_repo.add_user_to_table(table_name, user.id)

            await ctx.send(
                f"You have joined table '{table_name}' as '{username}'. You can now buy/sell chips."
            )

    @bot.command(name="leave")
    async def leave_cmd(ctx: commands.Context):
        external_ctx = _build_external_context(ctx.author)
        logout_external_identity(external_ctx, identity_repo)
        await ctx.send("You have been logged out on this account.")

    @bot.command(name="list")
    async def list_cmd(ctx: commands.Context, table_name: str):
        if not table_repo.exists(table_name):
            await ctx.send(f"Table '{table_name}' does not exist.")
            return

        user_ids = table_repo.get_user_ids_for_table(table_name)
        if not user_ids:
            await ctx.send(f"No players at table '{table_name}'.")
            return

        users = []
        for uid in user_ids:
            u = user_repo.get_user(uid)
            if u is not None:
                users.append(u)

        if not users:
            await ctx.send(f"No players at table '{table_name}'.")
            return

        lines = [f"{u.first_name}: {u.balance}" for u in users]
        total = sum(u.balance for u in users)
        lines.append(f"Total balance: {total}")
        await ctx.send("\n".join(lines))

    @bot.command(name="buy")
    async def buy_cmd(
        ctx: commands.Context,
        amount: int,
        username: str | None = None,
    ):
        """
        !buy <amount>         -> buy from bank
        !buy <amount> <user>  -> buy from another player by username
        """

        external_ctx = _build_external_context(ctx.author)

        if username is None:
            # Bank buy.
            result = buy_chips_from_bank(
                external_ctx,
                amount,
                identity_repo,
                user_repo,
            )
        else:
            result = buy_chips_from_user(
                external_ctx,
                amount,
                username,
                account_repo,
                identity_repo,
                user_repo,
            )

        if not result.success:
            await ctx.send(result.error_message or "Buy failed.")
            return

        text = (
            result.broadcasts[0].text
            if result.broadcasts
            else "Buy completed."
        )
        await ctx.send(text)

    @bot.command(name="sell")
    async def sell_cmd(
        ctx: commands.Context,
        amount: int,
        username: str | None = None,
    ):
        """
        !sell <amount>         -> sell to bank
        !sell <amount> <user>  -> sell to another player by username
        """

        external_ctx = _build_external_context(ctx.author)

        if username is None:
            result = sell_chips_to_bank(
                external_ctx,
                amount,
                identity_repo,
                user_repo,
            )
        else:
            result = sell_chips_to_user(
                external_ctx,
                amount,
                username,
                account_repo,
                identity_repo,
                user_repo,
            )

        if not result.success:
            await ctx.send(result.error_message or "Sell failed.")
            return

        text = (
            result.broadcasts[0].text
            if result.broadcasts
            else "Sell completed."
        )
        await ctx.send(text)

    return bot

