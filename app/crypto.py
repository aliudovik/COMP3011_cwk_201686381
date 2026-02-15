from cryptography.fernet import Fernet, InvalidToken

class TokenCipher:
    def __init__(self, key: str):
        self.enabled = bool(key)
        self._fernet = Fernet(key.encode("utf-8")) if self.enabled else None

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        if not self.enabled:
            return plaintext
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        if not self.enabled:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Backwards-compat: if older rows stored plaintext (no TOKEN_ENC_KEY at the time),
            # don't blank them out.
            # Fernet tokens almost always start with "gAAAA".
            if not ciphertext.startswith("gAAAA"):
                return ciphertext
            return ""

