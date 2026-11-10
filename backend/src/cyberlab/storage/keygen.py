"""Print a new random snapshot encryption key: ``python -m cyberlab.storage.keygen``."""

from cyberlab.storage.crypto import generate_key

if __name__ == "__main__":
    print(generate_key())
