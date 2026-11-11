"""Errors the HTTP layer maps to status codes."""


class LiminalError(Exception):
    """Base error for expected, user-facing failures."""

    status_code = 400


class InputError(LiminalError):
    """The selected input could not start or parse a source."""


class SessionError(LiminalError):
    """Recording start/stop was rejected."""

    status_code = 409


class ProfileError(LiminalError):
    """A band profile failed validation or could not be stored."""

    status_code = 422
