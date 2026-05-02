from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ServerStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    DELETING = "deleting"


class NodeStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DRAIN = "drain"
