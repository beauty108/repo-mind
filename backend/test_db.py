import asyncio
from sqlalchemy import select
from app.database import get_session_factory
from app.models.repository import Repository, RepositoryStatus

async def main():
    factory = get_session_factory()
    async with factory() as session:
        query = select(Repository).where(Repository.status == RepositoryStatus.failed).order_by(Repository.updated_at.desc()).limit(1)
        result = await session.execute(query)
        repo = result.scalars().first()
        if repo:
            print("FAILED REPO:", repo.repo_name)
            print("ERROR MESSAGE:", repo.error_message)
        else:
            print("No failed repositories found.")

asyncio.run(main())
