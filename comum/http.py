import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ServicoHTTPError(RuntimeError):
    pass


def requisitar_json(url, metodo="GET", dados=None, headers=None, timeout=5):
    cabecalhos = {"Accept": "application/json"}
    if headers:
        cabecalhos.update(headers)

    corpo = None
    if dados is not None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"

    requisicao = Request(url, data=corpo, headers=cabecalhos, method=metodo)
    try:
        with urlopen(requisicao, timeout=timeout) as resposta:
            conteudo = resposta.read()
    except HTTPError as erro:
        detalhe = erro.read(4096).decode("utf-8", errors="replace")
        raise ServicoHTTPError(
            f"HTTP {erro.code} ao chamar {url}: {detalhe}"
        ) from erro
    except (URLError, TimeoutError) as erro:
        raise ServicoHTTPError(f"Falha ao chamar {url}: {erro}") from erro

    if not conteudo:
        return {}

    try:
        resultado = json.loads(conteudo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ServicoHTTPError(
            f"A resposta de {url} nao contem JSON valido."
        ) from erro

    if not isinstance(resultado, dict):
        raise ServicoHTTPError(f"A resposta de {url} deve ser um objeto JSON.")
    return resultado
