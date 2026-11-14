"""Tables PostgreSQL."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Time
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(254), nullable=False, unique=True, index=True)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="USER")  # USER, AGENT ou ADMIN
    counter_id = Column(Integer, ForeignKey("counters.id"), nullable=True)  # guichet de l'agent
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False)  # UTC


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    opening_time = Column(Time, nullable=False)
    closing_time = Column(Time, nullable=False)
    ticket_cutoff_time = Column(Time, nullable=True)  # override historique, si défini
    ticket_cutoff_minutes_before_closing = Column(Integer, nullable=False, default=30, server_default="30")
    no_show_timeout_seconds = Column(Integer, nullable=False, default=120)
    is_active = Column(Boolean, nullable=False, default=True)


class Counter(Base):
    __tablename__ = "counters"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)

    service = relationship("Service")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_service_status", "service_id", "status"),)

    id = Column(Integer, primary_key=True)
    ticket_number = Column(String(10), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    counter_id = Column(Integer, ForeignKey("counters.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # compte propriétaire (facultatif)
    user_name = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="WAITING")
    created_at = Column(DateTime, nullable=False)  # UTC
    queue_at = Column(DateTime, nullable=False)    # UTC, ordre FIFO (change à la réactivation)
    called_at = Column(DateTime, nullable=True)    # UTC
    reactivation_count = Column(Integer, nullable=False, default=0)

    service = relationship("Service")


class TicketStatusHistory(Base):
    __tablename__ = "ticket_status_history"

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    previous_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=False)
    changed_at = Column(DateTime, nullable=False)  # UTC
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    ticket = relationship("Ticket")
