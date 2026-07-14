from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, JSON
import datetime
from .database import Base

class UserGoal(Base):
    __tablename__ = 'user_goals'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='PENDING')
    execution_plan: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)