"""DepositWatcherState model: tracks blockchain scanning state."""

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from app.database import Base


class DepositWatcherState(Base):
    """Tracks the last block scanned by the deposit watcher."""

    __tablename__ = "deposit_watcher_state"

    id = Column(Integer, primary_key=True, default=1)  # Single row table
    last_scanned_block = Column(Integer, nullable=False, default=0)
