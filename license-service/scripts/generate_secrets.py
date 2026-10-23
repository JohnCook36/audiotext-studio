"""Generate private license key and admin token locally. Never commit keys."""
import base64
import secrets
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

folder = Path(__file__).resolve().parent.parent / "keys"
folder.mkdir(parents=True, exist_ok=True)
if any(folder.iterdir()):
    raise SystemExit("Ключи уже существуют. Не генерируйте их повторно!")
private = Ed25519PrivateKey.generate()
values = {
    "private_key.pkcs8.b64": base64.b64encode(
        private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())).decode(),
    "public_key.raw.b64": base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
    "admin_token.txt": secrets.token_urlsafe(48),
}
for name, value in values.items():
    (folder / name).write_text(value + "\n", encoding="utf-8")
print("Ключи и администраторский токен созданы в папке keys/. Не публикуйте их.")
