from .base import ProviderAdapter

class AppleMusicProvider(ProviderAdapter):
    provider_name = "apple_music"
    # Stub: MusicKit is primarily client-driven; server may accept a user token and fetch recents later
