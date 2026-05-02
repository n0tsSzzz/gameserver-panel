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
