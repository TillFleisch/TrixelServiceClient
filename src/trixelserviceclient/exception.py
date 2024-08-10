"""Trixel Service Client related exceptions."""


class BaseError(RuntimeError):
    """Trixel service client base error from which related runtime errors are derived."""

    pass


class ServerError(BaseError):
    """Server side errors in which case a client may attempt to repeat a request."""

    pass


class InvalidStateError(BaseError):
    """Indicates that an action cannot be performed in the clients current state."""

    pass


class CriticalError(RuntimeError):
    """Indicate that the client is in a state where it should not further communicate with other components."""

    pass


class AuthenticationError(CriticalError):
    """Invalid authentication error, which indicates that the client should stop sending requests."""

    pass
