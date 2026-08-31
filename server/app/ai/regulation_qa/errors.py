REGULATION_QA_VERIFICATION_ERROR_CODE = 46001
REGULATION_QA_VERIFICATION_ERROR_MESSAGE = "regulation answer verification failed"


class RegulationCitationVerificationError(RuntimeError):
    """Raised when model output cannot pass the trusted citation boundary."""
