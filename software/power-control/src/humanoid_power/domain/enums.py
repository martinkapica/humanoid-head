from enum import StrEnum


class Profile(StrEnum):
    MONITOR = "MONITOR"
    DIRECT = "DIRECT"
    TIMED = "TIMED"


class OutletState(StrEnum):
    ON = "ON"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"


class DataQuality(StrEnum):
    GOOD = "GOOD"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


class ControllerStatus(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    AMBIGUOUS = "AMBIGUOUS"


class ModuleCondition(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    FAULT = "FAULT"
    CRITICAL = "CRITICAL"


class OperationKind(StrEnum):
    SET_OUTLET_STATE = "SET_OUTLET_STATE"
    WRITE_SCHEDULE = "WRITE_SCHEDULE"
    DELETE_SCHEDULE = "DELETE_SCHEDULE"
    RECONCILE = "RECONCILE"


class OperationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CONFIRMED = "CONFIRMED"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class OperationSource(StrEnum):
    WEB_MANUAL = "WEB_MANUAL"
    WEB_SCHEDULE = "WEB_SCHEDULE"
    SYSTEM = "SYSTEM"


class Criticality(StrEnum):
    NORMAL = "NORMAL"
    CRITICAL = "CRITICAL"


class ScheduleStatus(StrEnum):
    NONE = "NONE"
    ACTIVE = "ACTIVE"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


class RepeatMode(StrEnum):
    NONE = "NONE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    CUSTOM = "CUSTOM"


class EventAction(StrEnum):
    ON = "ON"
    OFF = "OFF"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
