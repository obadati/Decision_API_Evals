from decision_tests.adapters.jev import JevAdapter


class AzureAdapter(JevAdapter):
    """Microsoft-Decision-1: System One syntax and Azure resource API-key auth.

    The request model is the deployment name, not the catalog model ID.
    """

    def headers(self) -> dict[str, str]:
        return {"api-key": self.config.api_key}
