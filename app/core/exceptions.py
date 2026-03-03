"""
Custom exceptions for the PAE accounting system.
"""


class PAEException(Exception):
    """Base exception for PAE system."""
    pass


class InvalidNITException(PAEException):
    """Invalid NIT format."""
    pass


class FileProcessingException(PAEException):
    """Error processing uploaded file."""
    pass


class ProcessNotFoundException(PAEException):
    """Process ID not found in database."""
    pass


class IngestNotFoundException(PAEException):
    """Ingest ID not found in database."""
    pass


class ValidationException(PAEException):
    """Data validation error."""
    pass


class AgentException(PAEException):
    """Error in agent execution."""
    pass


class DatabaseException(PAEException):
    """Database operation error."""
    pass
