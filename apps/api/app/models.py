from enum import Enum
from datetime import datetime

from sqlalchemy import Column, Integer, DateTime, ForeignKey, Enum as SqlEnum
from app.database import Base


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    status = Column(
        SqlEnum(RunStatus, name="run_status"),
        nullable=False,
        default=RunStatus.QUEUED,
    )
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)