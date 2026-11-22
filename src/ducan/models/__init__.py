"""SQLAlchemy models. Importing this package registers every table on ``Base``."""

from ducan.models.actions import Action, ActionStatus, ActionType, AuditLog
from ducan.models.catalog import PriceHistory, Product, Supplier
from ducan.models.content import (
    ChatEscalation,
    ContentCalendarEntry,
    FaqEntry,
    Review,
)
from ducan.models.inventory import MovementKind, StockLevel, StockMovement
from ducan.models.jobs import JobRun
from ducan.models.pricing import CompetitorPrice
from ducan.models.sales import PaymentMethod, Sale, SalesImport
from ducan.models.settings import Setting
from ducan.models.staff import Shift, Staff, StaffAvailability, SwapRequest

__all__ = [
    "Action",
    "ActionStatus",
    "ActionType",
    "AuditLog",
    "ChatEscalation",
    "ContentCalendarEntry",
    "FaqEntry",
    "Review",
    "CompetitorPrice",
    "JobRun",
    "MovementKind",
    "PaymentMethod",
    "PriceHistory",
    "Product",
    "Sale",
    "SalesImport",
    "Setting",
    "Shift",
    "Staff",
    "StaffAvailability",
    "SwapRequest",
    "StockLevel",
    "StockMovement",
    "Supplier",
]
