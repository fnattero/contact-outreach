class ProspectPipelineInactive(RuntimeError):
    """Raised when an automatic prospect task observes a non-running campaign."""


class StaleProspectAnalysis(RuntimeError):
    """Raised when an older generation finishes after a newer request was reserved."""
