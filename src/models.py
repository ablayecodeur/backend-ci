"""Tables PostgreSQL."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Time
from sqlalchemy.orm import relationship

from .database import Base


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    opening_time = Column(Time, nullable=False)
    closing_time = Column(Time, nullable=False)
    no_show_timeout_seconds = Column(Integer, nullable=False, default=120)
    is_active = Column(Boolean, nullable=False, default=True)


class Counter(Base):
    __tablename__ = "counters"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    service = relationship("Service")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_service_status", "service_id", "status"),)

    id = Column(Integer, primary_key=True)
    ticket_number = Column(String(10), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    counter_id = Column(Integer, ForeignKey("counters.id"), nullable=True)
    user_name = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="WAITING")
    created_at = Column(DateTime, nullable=False)  # UTC
    queue_at = Column(DateTime, nullable=False)    # UTC, ordre FIFO (change à la réactivation)
    called_at = Column(DateTime, nullable=True)    # UTC

    service = relationship("Service")
