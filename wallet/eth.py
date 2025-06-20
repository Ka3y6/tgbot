from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from web3 import Web3
from eth_account import Account

from db.models import SessionLocal, User

backend = default_backend()

# Настройка сети Ethereum
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "https://rpc.ankr.com/eth")
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

PBKDF2_ITERATIONS = 250_000
AES_KEY_LENGTH = 32  # 256 бит


def _derive_key(password: str, salt: bytes) -> bytes:
    """Выводим ключ из пароля при помощи PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=AES_KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
        backend=backend,
    )
    return kdf.derive(password.encode())


def encrypt_private_key(private_key: bytes, password: str) -> Tuple[bytes, bytes]:
    """Шифруем приватный ключ AES-256-GCM. Возвращает (ciphertext, salt)."""
    salt = secrets.token_bytes(16)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ciphertext = nonce + aesgcm.encrypt(nonce, private_key, None)
    return ciphertext, salt


def decrypt_private_key(ciphertext: bytes, salt: bytes, password: str) -> bytes:
    """Расшифровываем приватный ключ."""
    key = _derive_key(password, salt)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None)


@dataclass
class WalletInfo:
    address: str
    balance_eth: float


# ---------- High-level API ---------- #

def create_wallet(telegram_id: int, password: str) -> WalletInfo:
    """Генерирует кошелёк, шифрует private key и сохраняет в базу."""

    acct = Account.create()
    priv_bytes = acct.key  # bytes
    ciphertext, salt = encrypt_private_key(priv_bytes, password)

    with SessionLocal() as session:
        user = session.get(User, telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id)
            session.add(user)
        user.address = acct.address
        user.encrypted_key = ciphertext
        user.salt = salt
        session.commit()

    return WalletInfo(address=acct.address, balance_eth=0)


def get_wallet(telegram_id: int) -> WalletInfo | None:
    with SessionLocal() as session:
        user = session.get(User, telegram_id)
        if user and user.address:
            balance_wei = w3.eth.get_balance(user.address)
            return WalletInfo(address=user.address, balance_eth=w3.from_wei(balance_wei, "ether"))
    return None


def send_eth(telegram_id: int, to_address: str, amount_eth: float, password: str) -> str:
    """Подписывает и отправляет транзакцию, возвращает hash."""

    with SessionLocal() as session:
        user = session.get(User, telegram_id)
        if not user or not user.encrypted_key:
            raise RuntimeError("Кошелёк не найден. Создайте его командой /createwallet")

        priv_key = decrypt_private_key(user.encrypted_key, user.salt, password)
        acct = Account.from_key(priv_key)
        if acct.address.lower() != user.address.lower():
            raise RuntimeError("Адрес кошелька не совпадает.")

        nonce = w3.eth.get_transaction_count(acct.address)
        value = w3.to_wei(amount_eth, "ether")
        gas_price = w3.eth.gas_price
        tx = {
            "to": Web3.to_checksum_address(to_address),
            "value": value,
            "gas": 21_000,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
        }

        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

        # Записываем txn в БД
        from db.models import Transaction  # локальный импорт, чтобы избежать циклов

        session.add(
            Transaction(
                user_id=telegram_id,
                tx_hash=tx_hash.hex(),
                direction="out",
                amount_eth=amount_eth,
            )
        )
        session.commit()

    return tx_hash.hex() 