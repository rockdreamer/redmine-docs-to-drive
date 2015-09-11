from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker

from celeryconfig import REDMINE_TO_DRIVE_DB_URL

engine = create_engine(
    REDMINE_TO_DRIVE_DB_URL, convert_unicode=True,
    pool_recycle=3600, pool_size=10)
db_session = scoped_session(sessionmaker(
    autocommit=False, autoflush=False, bind=engine))
