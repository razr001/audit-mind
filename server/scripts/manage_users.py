from __future__ import annotations

import argparse
import asyncio
import getpass

from app.core.exceptions import BusinessException
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.refresh_token_store import refresh_token_store
from app.repositories.user_repository import UserRepository
from app.services.user_management_service import UserManagementService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage AuditMind login users")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "set-password", "delete"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("username")
    subparsers.add_parser("list")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    async with async_session_factory() as session:
        service = UserManagementService(
            uow=UnitOfWork(session),
            repository=UserRepository(session),
            refresh_store=refresh_token_store,
        )
        if args.command == "list":
            users = await service.list_users()
            for user in users:
                print(f"{user.id}  {user.username}")
            return
        if args.command == "delete":
            await service.delete(args.username)
            print(f"deleted user: {args.username}")
            return

        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("passwords do not match")
        if args.command == "create":
            user = await service.create(args.username, password)
            print(f"created user: {user.username} ({user.id})")
        else:
            user = await service.change_password(args.username, password)
            print(f"updated password: {user.username}")


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except (ValueError, BusinessException) as exc:
        message = exc.message if isinstance(exc, BusinessException) else str(exc)
        raise SystemExit(message) from exc
