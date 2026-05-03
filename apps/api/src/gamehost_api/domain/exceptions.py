class DomainError(Exception):
    code: str = "domain_error"
    status_code: int = 400
    title: str = "Domain error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class InvalidCredentials(DomainError):
    code = "invalid_credentials"
    status_code = 401
    title = "Invalid credentials"


class EmailAlreadyTaken(DomainError):
    code = "email_taken"
    status_code = 409
    title = "Email is already registered"


class RefreshInvalid(DomainError):
    code = "refresh_invalid"
    status_code = 401
    title = "Refresh token invalid or expired"


class UserInactive(DomainError):
    code = "user_inactive"
    status_code = 401
    title = "User is inactive"


class Forbidden(DomainError):
    code = "forbidden"
    status_code = 403
    title = "Forbidden"


class TemplateNotFound(DomainError):
    code = "template_not_found"
    status_code = 404
    title = "Game template not found"


class SlugAlreadyTaken(DomainError):
    code = "slug_taken"
    status_code = 409
    title = "Template slug already taken"


class NodeNotFound(DomainError):
    code = "node_not_found"
    status_code = 404
    title = "Node not found"


class NodeNameTaken(DomainError):
    code = "node_name_taken"
    status_code = 409
    title = "Node name already taken"


class ServerNotFound(DomainError):
    code = "server_not_found"
    status_code = 404
    title = "Server not found"


class NoCapacity(DomainError):
    code = "no_capacity"
    status_code = 409
    title = "No node has capacity for the requested resources"


class InvalidServerState(DomainError):
    code = "invalid_server_state"
    status_code = 409
    title = "Server is in an invalid state for this operation"


class TaskNotFound(DomainError):
    code = "task_not_found"
    status_code = 404
    title = "Task not found"
