import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from .config import PASTA_CHAVES


def _arquivo_chave(servico, nome):
    return PASTA_CHAVES / servico / nome


def garantir_chaves(servico):
    """Cria as chaves do servico na primeira execucao."""
    pasta = PASTA_CHAVES / servico
    pasta.mkdir(parents=True, exist_ok=True)

    arquivo_privada = _arquivo_chave(servico, "private_key.pem")
    arquivo_publica = _arquivo_chave(servico, "public_key.pem")

    if arquivo_privada.exists() and arquivo_publica.exists():
        return

    # A chave publica fica disponivel para os outros consumidores.
    chave_privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    chave_publica = chave_privada.public_key()

    arquivo_privada.write_bytes(
        chave_privada.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    arquivo_publica.write_bytes(
        chave_publica.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _conteudo_assinado(evento, produtor, dados):
    conteudo = {
        "Event": evento,
        "Producer": produtor,
        "Data": dados,
    }
    return json.dumps(conteudo, sort_keys=True, separators=(",", ":")).encode()


def _gerar_hash(conteudo):
    digest = hashes.Hash(hashes.SHA256())
    digest.update(conteudo)
    return digest.finalize()


def criar_envelope(produtor, evento, dados):
    garantir_chaves(produtor)
    conteudo = _conteudo_assinado(evento, produtor, dados)
    hash_conteudo = _gerar_hash(conteudo)

    chave_privada = serialization.load_pem_private_key(
        _arquivo_chave(produtor, "private_key.pem").read_bytes(),
        password=None,
    )
    # O RSA assina o hash, e nao o JSON inteiro.
    assinatura = chave_privada.sign(
        hash_conteudo,
        padding.PKCS1v15(),
        utils.Prehashed(hashes.SHA256()),
    )

    return {
        "Event": evento,
        "Producer": produtor,
        "Data": dados,
        "Signature": base64.b64encode(assinatura).decode(),
    }


def validar_envelope(envelope):
    produtor = envelope["Producer"]
    evento = envelope["Event"]
    dados = envelope["Data"]
    assinatura = base64.b64decode(envelope["Signature"])
    arquivo_publica = _arquivo_chave(produtor, "public_key.pem")

    chave_publica = serialization.load_pem_public_key(arquivo_publica.read_bytes())
    conteudo = _conteudo_assinado(evento, produtor, dados)
    hash_conteudo = _gerar_hash(conteudo)

    # Se qualquer campo tiver mudado, a verificacao falha.
    try:
        chave_publica.verify(
            assinatura,
            hash_conteudo,
            padding.PKCS1v15(),
            utils.Prehashed(hashes.SHA256()),
        )
    except InvalidSignature:
        return False

    return True
