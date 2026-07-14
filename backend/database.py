from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://os_admin:local_secure_pwd@localhost:5432/info_os")

engine = create_engine(DATABASE_URL, echo=True)

class Base(DeclarativeBase):
    pass