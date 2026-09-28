from enum import Enum


class PlatformType(str, Enum):
    TELEGRAM = "TELEGRAM"
    THREADS = "THREADS"
    MANUAL = "MANUAL"
    WEBHOOK = "WEBHOOK"


class SenderPolicy(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    DEFAULT = "DEFAULT"


class DeliveryStatus(str, Enum):
    EMITTED = "EMITTED"
    BLOCKED = "BLOCKED"
    IGNORED = "IGNORED"
    ERROR = "ERROR"
