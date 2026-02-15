class ProviderAdapter:
    provider_name = "base"

    def build_authorize_url(self):
        raise NotImplementedError

    def exchange_code_for_token(self, code: str, **kwargs):
        raise NotImplementedError

    def ingest_representative_tracks(self, access_token: str):
        raise NotImplementedError
