"""Genera de forma interactiva APP_AUTH_PASSWORD_HASH para el fichero .env."""

from getpass import getpass

from backend.auth import hash_password

password = getpass("Nueva contraseña de acceso: ")
confirmation = getpass("Repite la contraseña: ")
if password != confirmation:
    raise SystemExit("Las contraseñas no coinciden")
if len(password) < 10:
    raise SystemExit("La contraseña debe tener al menos 10 caracteres")
print(hash_password(password))
