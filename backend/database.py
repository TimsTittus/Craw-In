from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "postgresql://os_admin:local_secure_pwd@localhost:5432/info_os"

engine = create_engine(DATABASE_URL, echo=True)

class Base(DeclarativeBase):
    pass