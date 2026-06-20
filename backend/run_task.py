import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.config import get_settings
from app.worker.tasks import clone_and_index
from app.models.repository import Repository, RepositoryStatus
import logging

logging.basicConfig(level=logging.INFO)

def run_test():
    settings = get_settings()
    sync_engine = create_engine(settings.sync_database_url)

    with Session(sync_engine) as db:
        repo = db.query(Repository).filter(Repository.status == RepositoryStatus.failed).order_by(Repository.updated_at.desc()).first()
        if not repo:
            print("No failed repo found.")
            return
        print(f"Retrying indexing for repo: {repo.github_url}")
        
        # Reset status
        repo.status = RepositoryStatus.pending
        repo.error_message = None
        db.commit()
        
        repo_id = str(repo.id)
        
    try:
        # Call the task directly (synchronously)
        clone_and_index(repo_id)
    except Exception as e:
        print("TASK RAISED EXCEPTION:", e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
