"""
Meu primeiro bot — 3B Connect, Receita 01.

Como ligar:   python meu_bot.py      (no Mac/Linux: python3 meu_bot.py)
Como desligar: aperte Ctrl + C na janela do terminal.

O bot tem dois modos:
  1. ECO      — só repete o que você manda. Serve para provar que o
                Telegram chegou até o seu computador. Não precisa instalar nada.
  2. CÉREBRO  — responde com IA (Claude), seguindo o que está em persona.txt.
                Liga sozinho quando você coloca ANTHROPIC_API_KEY em segredos.txt.

Você não precisa entender este arquivo para usar o bot.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO_SEGREDOS = PASTA / "segredos.txt"
ARQUIVO_PERSONA = PASTA / "persona.txt"

# Endereço do Telegram. Só muda em teste automatizado.
TELEGRAM_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")

MODELO = "claude-opus-5-5"
MEMORIA_MAXIMA = 10  # quantas mensagens recentes de cada conversa o bot lembra


# ---------------------------------------------------------------------------
# Falas dos guias no terminal
# ---------------------------------------------------------------------------

def terra(texto):
    print(f"\n🟢 Terra: {texto}", flush=True)


def pulso(texto):
    print(f"\n🟣 Pulso: {texto}", flush=True)


def faisca(texto):
    print(f"\n🟡 Faísca: {texto}", flush=True)


def parar(texto, codigo):
    terra(texto)
    print("\n   O bot parou. Corrija e rode o comando de novo.\n", flush=True)
    sys.exit(codigo)


# ---------------------------------------------------------------------------
# segredos.txt
# ---------------------------------------------------------------------------

def ler_segredos():
    if not ARQUIVO_SEGREDOS.exists():
        if (PASTA / "segredos.txt.txt").exists():
            parar(
                "Achei um arquivo chamado 'segredos.txt.txt' (com .txt duas vezes).\n"
                "   O Windows esconde a extensão e o Bloco de Notas acrescenta outra.\n"
                "   Renomeie para 'segredos.txt' e tente de novo.",
                2,
            )
        parar(
            "Não encontrei o arquivo segredos.txt nesta pasta.\n"
            "   Faça uma cópia de 'segredos.exemplo.txt', renomeie a cópia para\n"
            "   'segredos.txt' e cole o token do BotFather dentro dela.",
            2,
        )

    valores = {}
    texto = ARQUIVO_SEGREDOS.read_text(encoding="utf-8-sig")
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        valores[chave.strip().upper()] = valor.strip().strip('"').strip("'").strip()
    return valores


def conferir_token(token):
    if not token:
        parar(
            "O segredos.txt existe, mas a linha TELEGRAM_TOKEN= está vazia.\n"
            "   Cole o token logo depois do sinal de igual, sem espaço. Exemplo:\n"
            "   TELEGRAM_TOKEN=123456789:AAH...",
            2,
        )
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
        parar(
            "Esse texto não parece um token do Telegram.\n"
            "   Um token tem números, dois-pontos e uma sequência longa de letras,\n"
            "   tudo junto. Volte ao BotFather e copie a linha inteira de novo.",
            2,
        )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class ErroTelegram(Exception):
    pass


def telegram(token, metodo, dados=None, espera=35):
    url = f"{TELEGRAM_API}/bot{token}/{metodo}"
    corpo = json.dumps(dados or {}).encode("utf-8")
    pedido = urllib.request.Request(
        url, data=corpo, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=espera) as resposta:
            return json.loads(resposta.read().decode("utf-8"))["result"]
    except urllib.error.HTTPError as erro:
        if erro.code in (401, 404):
            parar(
                "O Telegram não aceitou esse token.\n"
                "   Copie de novo no BotFather (ou peça um novo com /token)\n"
                "   e substitua a linha no segredos.txt.",
                3,
            )
        if erro.code == 409:
            parar(
                "Já existe outra cópia deste bot ligada.\n"
                "   Procure outra janela de terminal rodando o bot e aperte Ctrl + C\n"
                "   nela. Só pode existir uma cópia ligada por vez.",
                6,
            )
        raise ErroTelegram(f"o Telegram respondeu com erro {erro.code}") from erro
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroTelegram("não consegui falar com o Telegram") from erro


def enviar(token, chat_id, texto):
    telegram(token, "sendMessage", {"chat_id": chat_id, "text": texto[:4000]})


# ---------------------------------------------------------------------------
# Trava de privacidade: números sensíveis nunca são gravados nem logados
# ---------------------------------------------------------------------------

def _passa_no_luhn(digitos):
    soma = 0
    for i, d in enumerate(reversed(digitos)):
        n = int(d)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        soma += n
    return soma % 10 == 0


def _trocar_numeros(trecho, rotulo, minimo, checar_luhn=False):
    digitos = re.sub(r"\D", "", trecho.group(0))
    if len(digitos) < minimo or (checar_luhn and not _passa_no_luhn(digitos)):
        return trecho.group(0)
    texto = trecho.group(0)
    inicio = texto[: len(texto) - len(texto.lstrip())]
    fim = texto[len(texto.rstrip()):]
    return f"{inicio}[{rotulo} removido]{fim}"


def mascarar(texto):
    """Troca CPF, CNPJ, cartão, e-mail e telefone por um aviso. Devolve (texto, mudou)."""
    novo = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "[CNPJ removido]", texto)
    novo = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "[CPF removido]", novo)
    novo = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[e-mail removido]", novo)
    novo = re.sub(r"(?:\d[ -]?){13,19}", lambda t: _trocar_numeros(t, "cartão", 13, True), novo)
    novo = re.sub(r"[\d\s().+-]{10,}", lambda t: _trocar_numeros(t, "número", 10), novo)
    return novo, novo != texto


# ---------------------------------------------------------------------------
# Cérebro (Claude)
# ---------------------------------------------------------------------------

def preparar_cerebro(chave):
    if not chave:
        return None
    try:
        import anthropic
    except ImportError:
        if sys.platform.startswith("win"):
            comandos = "       python -m pip install anthropic"
        else:
            comandos = (
                "       python3 -m venv .venv\n"
                "       .venv/bin/python -m pip install anthropic\n\n"
                "   e depois ligue o bot com:  .venv/bin/python meu_bot.py"
            )
        terra(
            "Você colocou a chave da IA, mas falta instalar a peça que conversa\n"
            f"   com ela. Desligue o bot (Ctrl + C) e rode:\n\n{comandos}\n\n"
            "   Por enquanto vou continuar no modo ECO."
        )
        return None
    return anthropic.Anthropic(api_key=chave)


def ler_persona():
    if ARQUIVO_PERSONA.exists():
        return ARQUIVO_PERSONA.read_text(encoding="utf-8-sig").strip()
    return "Você é um assistente educado. Responda em português, de forma curta."


def pensar(cliente, persona, historico):
    import anthropic

    try:
        # "fallbacks": se o modelo recusar o pedido, a própria Anthropic tenta
        # outro modelo recomendado antes de devolver a recusa.
        resposta = cliente.beta.messages.create(
            model=MODELO,
            max_tokens=1024,
            system=persona,
            messages=historico,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        terra("A chave da IA foi recusada. Confira ANTHROPIC_API_KEY no segredos.txt.")
        return "Estou sem acesso ao meu cérebro agora. Avise o meu dono, por favor."
    except anthropic.PermissionDeniedError:
        terra("A conta da IA não liberou o pedido. Confira se há crédito e limite no painel.")
        return "Estou sem acesso ao meu cérebro agora. Avise o meu dono, por favor."
    except anthropic.RateLimitError:
        terra("Muitos pedidos seguidos ou limite de gasto atingido. Espere um pouco.")
        return "Estou recebendo muitas mensagens. Tente de novo em um minuto."
    except anthropic.APIConnectionError:
        terra("Não consegui falar com a IA. Confira sua internet.")
        return "Tive um problema de conexão. Tente de novo em instantes."
    except anthropic.APIStatusError as erro:
        terra(f"A IA respondeu com erro {erro.status_code}. Tente de novo daqui a pouco.")
        return "Tive um problema para pensar nessa. Tente de novo em instantes."

    if resposta.stop_reason == "refusal":
        return "Prefiro não responder a isso. Posso ajudar com outra coisa?"
    texto = "".join(b.text for b in resposta.content if b.type == "text").strip()
    return texto or "Não consegui formular uma resposta. Pode perguntar de outro jeito?"


# ---------------------------------------------------------------------------
# Conversa
# ---------------------------------------------------------------------------

def responder(token, cerebro, persona, memoria, mensagem):
    chat_id = mensagem["chat"]["id"]
    nome = mensagem.get("from", {}).get("first_name", "você")
    texto = mensagem.get("text")

    if texto is None:
        enviar(token, chat_id, "Por enquanto eu só entendo mensagens de texto. 🙂")
        return

    texto, _ = mascarar(texto)  # o log e a IA nunca recebem CPF, cartão etc.
    print(f"   📩 {nome} mandou: {texto[:60]}", flush=True)

    if texto.startswith("/start"):
        memoria.pop(chat_id, None)
        modo = "com cérebro de IA" if cerebro else "no modo ECO (só repito)"
        enviar(token, chat_id, f"Oi, {nome}! Estou ligado {modo}. Manda uma mensagem.")
        return

    if texto.startswith("/esquecer"):
        memoria.pop(chat_id, None)
        enviar(token, chat_id, "Pronto, esqueci a nossa conversa. Começamos do zero.")
        return

    if not cerebro:
        enviar(token, chat_id, f"🔁 Você disse: {texto}")
        return

    historico = memoria.setdefault(chat_id, [])
    historico.append({"role": "user", "content": texto})
    resposta = pensar(cerebro, persona, historico)
    historico.append({"role": "assistant", "content": resposta})
    del historico[:-MEMORIA_MAXIMA]
    enviar(token, chat_id, resposta)
    print(f"   💬 Bot respondeu: {resposta[:60]}", flush=True)


def main():
    segredos = ler_segredos()
    token = segredos.get("TELEGRAM_TOKEN", "")
    conferir_token(token)

    pulso("Conferindo o token com o Telegram...")
    try:
        eu = telegram(token, "getMe", espera=15)
    except ErroTelegram:
        parar("Não consegui falar com o Telegram. Confira sua internet e tente de novo.", 4)

    cerebro = preparar_cerebro(segredos.get("ANTHROPIC_API_KEY", ""))
    persona = ler_persona()

    terra(f"Tudo certo! Seu bot @{eu['username']} está ligado e ouvindo.")
    if cerebro:
        pulso("Modo CÉREBRO: vou responder com IA seguindo o persona.txt.")
    else:
        pulso("Modo ECO: vou só repetir o que receber. É o teste do encanamento.")
    faisca(f"Abra o Telegram, procure @{eu['username']} e mande um oi!")
    if sys.stdin is not None and sys.stdin.isatty():
        print("\n   (Para desligar, aperte Ctrl + C nesta janela.)\n", flush=True)

    memoria = {}
    proxima = None
    falhas_seguidas = 0
    while True:
        try:
            pedido = {"timeout": 30, "allowed_updates": ["message"]}
            if proxima is not None:
                pedido["offset"] = proxima
            novidades = telegram(token, "getUpdates", pedido)
            falhas_seguidas = 0
        except ErroTelegram as erro:
            falhas_seguidas += 1
            espera = min(5 * falhas_seguidas, 60)
            terra(f"Perdi a conexão ({erro}). Tento de novo em {espera} segundos...")
            time.sleep(espera)
            continue

        for novidade in novidades:
            proxima = novidade["update_id"] + 1
            mensagem = novidade.get("message")
            if not mensagem:
                continue
            try:
                responder(token, cerebro, persona, memoria, mensagem)
            except ErroTelegram as erro:
                terra(f"Não consegui entregar a resposta ({erro}).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        faisca("Bot desligado. Até a próxima! 👋\n")
