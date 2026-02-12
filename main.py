import os
import threading

from dotenv import load_dotenv

from infrastructure.db.account_repository_sqlite import SqliteAccountRepository
from infrastructure.db.identity_repository_sqlite import SqliteIdentityRepository
from infrastructure.db.table_repository_sqlite import SqliteTableRepository
from infrastructure.db.user_repository_sqlite import SqliteUserRepository
from interfaces.discord.handlers import create_discord_bot
from interfaces.telegram.handlers import create_telegram_bot


load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "poker.db")


def _run_telegram_bot(
    user_repo: SqliteUserRepository,
    identity_repo: SqliteIdentityRepository,
    account_repo: SqliteAccountRepository,
    table_repo: SqliteTableRepository,
) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    bot = create_telegram_bot(BOT_TOKEN, user_repo, identity_repo, account_repo, table_repo)
    bot.infinity_polling()


def _run_discord_bot(
    user_repo: SqliteUserRepository,
    identity_repo: SqliteIdentityRepository,
    account_repo: SqliteAccountRepository,
) -> None:
    # Discord is optional – only start it if a token is configured.
    if not DISCORD_TOKEN:
        return

    table_repo = SqliteTableRepository(DB_PATH)

    bot = create_discord_bot(user_repo, identity_repo, account_repo, table_repo)
    bot.run(DISCORD_TOKEN)


def main() -> None:
    user_repo = SqliteUserRepository(DB_PATH)
    identity_repo = SqliteIdentityRepository(DB_PATH, user_repo)
    account_repo = SqliteAccountRepository(DB_PATH)
    table_repo = SqliteTableRepository(DB_PATH)

    threads = [
        threading.Thread(
            target=_run_telegram_bot,
            args=(user_repo, identity_repo, account_repo, table_repo),
            name="telegram-bot",
            daemon=False,
        ),
        threading.Thread(
            target=_run_discord_bot,
            args=(user_repo, identity_repo, account_repo),
            name="discord-bot",
            daemon=False,
        ),
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()

