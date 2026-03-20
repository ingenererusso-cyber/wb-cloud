class SupplyRecommendationsError(Exception):
    """Base module exception."""


class InvalidInputError(SupplyRecommendationsError):
    """Raised when input data is invalid."""


class MissingSellerError(SupplyRecommendationsError):
    """Raised when seller context is required but missing."""
