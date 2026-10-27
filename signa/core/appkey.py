"""La clave de la app en el llavero del sistema (Credential Manager / Llavero de Mac): la usan la app y el ejecutor."""
import keyring

KEYRING_SERVICE = "Signa"
KEYRING_USER = "app-key"


def saved_key() -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except keyring.errors.KeyringError:
        return None


def store_key(key: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)


def forget_key():
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except keyring.errors.KeyringError:
        pass
