"""
Meu bot com memória — 3B Connect, Receita 03 (versão 2 do meu_bot.py).

Como ligar:    python meu_bot.py      (no Mac/Linux: python3 meu_bot.py)
Como desligar: aperte Ctrl + C na janela do terminal.

O que mudou desde a Receita 01:
  - O bot tem um CADERNINHO por cliente (a "ficha"). Ele mesmo decide anotar
    o que for útil, mas só nos campos que você liberou em memoria.txt.
  - Uma TRAVA EM CÓDIGO confere cada anotação antes de salvar: documento,
    cartão, senha, e-mail, telefone e dado de saúde nunca são guardados,
    mesmo que a IA tente.
  - A conversa recente sobrevive a reinícios (fica em dados/memoria.sqlite3).
  - O cliente vê o que foi anotado com /minhaficha e apaga tudo com /esquecer.
  - Fichas sem conversa há mais de 180 dias são apagadas sozinhas.

Você não precisa entender este arquivo para usar o bot.
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO_SEGREDOS = PASTA / "segredos.txt"
ARQUIVO_PERSONA = PASTA / "persona.txt"
ARQUIVO_REGRAS_MEMORIA = PASTA / "memoria.txt"
PASTA_DADOS = PASTA / "dados"
BANCO = PASTA_DADOS / "memoria.sqlite3"

# Endereço do Telegram. Só muda em teste automatizado.
TELEGRAM_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")

MODELO = "claude-opus-5-5"
HISTORICO_MAXIMO = 20      # mensagens recentes guardadas por conversa
DIAS_PARA_ESQUECER = 180   # ficha sem conversa por mais tempo que isso é apagada
TAMANHO_MAXIMO_ANOTACAO = 120
VOLTAS_MAXIMAS = 4         # quantas vezes seguidas a IA pode usar o caderninho

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
# Memória: o caderninho de cada cliente
# ---------------------------------------------------------------------------

def ler_campos_permitidos():
    """Lê memoria.txt: uma linha por campo, no formato  campo: explicação."""
    campos = {}
    if ARQUIVO_REGRAS_MEMORIA.exists():
        for linha in ARQUIVO_REGRAS_MEMORIA.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or ":" not in linha:
                continue
            campo, explicacao = linha.split(":", 1)
            campo = re.sub(r"[^a-z0-9_]", "_", campo.strip().lower())
            if campo:
                campos[campo] = explicacao.strip()
    return campos


class Memoria:
    """Guarda a ficha e a conversa recente de cada cliente num arquivo local."""

    def __init__(self, caminho):
        caminho.parent.mkdir(mode=0o700, exist_ok=True)
        self.banco = sqlite3.connect(caminho)
        # Quando o cliente pede /esquecer, o dado é sobrescrito, não só "escondido".
        self.banco.execute("PRAGMA secure_delete = ON")
        self.banco.executescript(
            """
            CREATE TABLE IF NOT EXISTS ficha (
                chat_id INTEGER, campo TEXT, valor TEXT, atualizado_em TEXT,
                PRIMARY KEY (chat_id, campo));
            CREATE TABLE IF NOT EXISTS historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, papel TEXT, texto TEXT, em TEXT);
            CREATE INDEX IF NOT EXISTS historico_chat ON historico (chat_id, id);
            """
        )
        try:
            os.chmod(caminho, 0o600)
        except OSError:
            pass

    @staticmethod
    def _agora():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def ficha(self, chat_id):
        linhas = self.banco.execute(
            "SELECT campo, valor FROM ficha WHERE chat_id = ? ORDER BY campo", (chat_id,)
        )
        return dict(linhas.fetchall())

    def anotar(self, chat_id, campo, valor):
        with self.banco:
            self.banco.execute(
                "INSERT INTO ficha VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id, campo) DO UPDATE SET "
                "valor = excluded.valor, atualizado_em = excluded.atualizado_em",
                (chat_id, campo, valor, self._agora()),
            )

    def historico(self, chat_id):
        linhas = self.banco.execute(
            "SELECT papel, texto FROM historico WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, HISTORICO_MAXIMO),
        ).fetchall()
        mensagens = [{"role": papel, "content": texto} for papel, texto in reversed(linhas)]
        # A conversa enviada à IA precisa começar com uma fala do cliente.
        while mensagens and mensagens[0]["role"] != "user":
            mensagens.pop(0)
        return mensagens

    def lembrar_fala(self, chat_id, papel, texto):
        with self.banco:
            self.banco.execute(
                "INSERT INTO historico (chat_id, papel, texto, em) VALUES (?, ?, ?, ?)",
                (chat_id, papel, texto, self._agora()),
            )
            self.banco.execute(
                "DELETE FROM historico WHERE chat_id = ? AND id NOT IN ("
                "SELECT id FROM historico WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
                (chat_id, chat_id, HISTORICO_MAXIMO),
            )

    def esquecer(self, chat_id):
        with self.banco:
            self.banco.execute("DELETE FROM ficha WHERE chat_id = ?", (chat_id,))
            self.banco.execute("DELETE FROM historico WHERE chat_id = ?", (chat_id,))

    def apagar_antigas(self):
        limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_PARA_ESQUECER)).isoformat()
        ativos = "SELECT chat_id FROM historico WHERE em >= ? UNION SELECT chat_id FROM ficha WHERE atualizado_em >= ?"
        with self.banco:
            antes = self.banco.total_changes
            self.banco.execute(f"DELETE FROM ficha WHERE chat_id NOT IN ({ativos})", (limite, limite))
            self.banco.execute(f"DELETE FROM historico WHERE chat_id NOT IN ({ativos})", (limite, limite))
            return self.banco.total_changes - antes


# --- A trava: o que NUNCA vai para o caderninho -----------------------------

# Lista de exemplo, não exaustiva: a persona também proíbe dado sensível.
# Saúde, religião, política e orientação sexual são "dados sensíveis" na LGPD.
# Cuidado para não bloquear o próprio negócio: "gestante" ficou de fora porque
# "ensaio de gestante" é um serviço comum de estúdio.
PALAVRAS_SENSIVEIS = (
    "senha", "password", "cvv", "token", "chave pix", "cartão", "cartao",
    "diagnóstico", "diagnostico", "doença", "doenca", "remédio", "remedio",
    "medicamento", "tratamento", "cirurgia", "diabet", "diabét", "hipertens",
    "pressão alta", "alergia", "alérgic", "alergic",
    "depressão", "depressao", "ansiedade", "câncer", "cancer", "hiv", "deficiência",
    "deficiencia", "religião", "religiao", "partido", "orientação sexual",
)


def motivo_para_recusar(valor):
    """Devolve o motivo se o texto parecer dado sensível; None se puder guardar."""
    texto = valor.lower()
    if re.search(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", valor):
        return "parece um CPF"
    if re.search(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", valor):
        return "parece um CNPJ"
    for trecho in re.findall(r"(?:\d[ -]?){13,19}", valor):
        digitos = re.sub(r"\D", "", trecho)
        if 13 <= len(digitos) <= 19 and _passa_no_luhn(digitos):
            return "parece um número de cartão"
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", valor):
        return "parece um e-mail"
    for trecho in re.findall(r"[\d\s().+-]{10,}", valor):
        if len(re.sub(r"\D", "", trecho)) >= 10:
            return "parece um telefone ou documento"
    for palavra in PALAVRAS_SENSIVEIS:
        if palavra in texto:
            return f"fala de '{palavra}', que é dado sensível"
    return None


def executar_anotacao(memoria, campos, chat_id, entrada):
    """Roda quando a IA pede para anotar. Só salva o que passar pela trava."""
    campo = str(entrada.get("campo", ""))
    valor = " ".join(str(entrada.get("valor", "")).split())
    if campo not in campos:
        return False, f"Recusado: '{campo}' não é um campo permitido. Não foi salvo."
    if not valor:
        return False, "Recusado: anotação vazia."
    if len(valor) > TAMANHO_MAXIMO_ANOTACAO:
        return False, f"Recusado: anotação longa demais (máximo {TAMANHO_MAXIMO_ANOTACAO} letras). Resuma."
    motivo = motivo_para_recusar(valor)
    if motivo:
        terra(f"🛡️ Trava: a IA tentou anotar algo que {motivo}. Não foi salvo.")
        return False, f"Recusado pela trava de segurança: {motivo}. Não foi salvo. Não peça esse dado."
    memoria.anotar(chat_id, campo, valor)
    pulso(f"📝 Anotado na ficha: {campo}")
    return True, "Anotado."


def ferramenta_do_caderninho(campos):
    lista = "\n".join(f"- {c}: {e}" for c, e in campos.items())
    return {
        "name": "anotar_na_ficha",
        "description": (
            "Anota UMA informação útil sobre o cliente atual, para lembrar nas próximas "
            "conversas. Use só quando o cliente contar algo que se encaixe num destes campos:\n"
            f"{lista}\n"
            "Nunca anote documento, cartão, senha, e-mail, telefone ou dado de saúde. "
            "Anote de forma curta. Se já houver valor no campo, o novo substitui o antigo."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "campo": {"type": "string", "enum": list(campos)},
                "valor": {"type": "string"},
            },
            "required": ["campo", "valor"],
            "additionalProperties": False,
        },
    }


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


def montar_instrucoes(persona, ficha, campos):
    if campos:
        caderninho = (
            "CADERNINHO\n"
            "Você tem a ferramenta anotar_na_ficha para lembrar o que o cliente contar "
            "nos campos permitidos. Não diga ao cliente que está anotando, a menos que ele pergunte. "
            "Se ele perguntar o que você sabe sobre ele, diga para enviar /minhaficha. "
            "Se pedir para esquecer, diga para enviar /esquecer.\n"
            "Você só enxerga a ficha DESTE cliente. Nunca fale de outros clientes."
        )
    else:
        caderninho = "Você não guarda anotações sobre os clientes."
    if ficha:
        sabido = "\n".join(f"- {c}: {v}" for c, v in ficha.items())
    else:
        sabido = "(vazia: é a primeira conversa ou o cliente pediu para esquecer)"
    return f"{persona}\n\n---\n{caderninho}\n\nFICHA DESTE CLIENTE:\n{sabido}"


def pensar(cliente, instrucoes, historico, ferramentas, ao_anotar):
    import anthropic

    mensagens = list(historico)
    try:
        for _ in range(VOLTAS_MAXIMAS):
            # "fallbacks": se o modelo recusar o pedido, a própria Anthropic tenta
            # outro modelo recomendado antes de devolver a recusa.
            resposta = cliente.beta.messages.create(
                model=MODELO,
                max_tokens=1024,
                system=instrucoes,
                messages=mensagens,
                tools=ferramentas,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resposta.stop_reason != "tool_use":
                break
            # A IA quer usar o caderninho: devolvemos o pedido dela e o resultado.
            mensagens.append({"role": "assistant", "content": resposta.content})
            resultados = []
            for bloco in resposta.content:
                if bloco.type == "tool_use":
                    ok, texto = ao_anotar(bloco.input)
                    resultados.append({
                        "type": "tool_result",
                        "tool_use_id": bloco.id,
                        "content": texto,
                        "is_error": not ok,
                    })
            mensagens.append({"role": "user", "content": resultados})
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

def mostrar_ficha(ficha):
    if not ficha:
        return "Não tenho nenhuma anotação sobre você. 🙂"
    linhas = "\n".join(f"• {c}: {v}" for c, v in ficha.items())
    return f"Isto é tudo o que anotei sobre você:\n{linhas}\n\nPara apagar tudo, envie /esquecer."


def responder(token, cerebro, persona, campos, memoria, mensagem):
    chat_id = mensagem["chat"]["id"]
    nome = mensagem.get("from", {}).get("first_name", "você")
    texto = mensagem.get("text")

    if texto is None:
        enviar(token, chat_id, "Por enquanto eu só entendo mensagens de texto. 🙂")
        return

    texto, mascarou = mascarar(texto)
    print(f"   📩 {nome} mandou: {texto[:60]}", flush=True)
    if mascarou:
        terra("🛡️ Trava: a mensagem tinha um número sensível. Mascarei antes de guardar.")

    if texto.startswith("/start"):
        modo = "com cérebro de IA" if cerebro else "no modo ECO (só repito)"
        aviso = (
            "\n\nPara te atender melhor, eu guardo algumas anotações sobre a nossa conversa. "
            "Veja com /minhaficha e apague tudo quando quiser com /esquecer."
            if cerebro and campos else ""
        )
        enviar(token, chat_id, f"Oi, {nome}! Estou ligado {modo}.{aviso}")
        return

    if texto.startswith("/minhaficha"):
        enviar(token, chat_id, mostrar_ficha(memoria.ficha(chat_id)))
        return

    if texto.startswith("/esquecer"):
        memoria.esquecer(chat_id)
        pulso("🧹 Um cliente pediu /esquecer: ficha e conversa apagadas.")
        enviar(token, chat_id, "Pronto, apaguei tudo o que eu sabia sobre você e a nossa conversa.")
        return

    if not cerebro:
        enviar(token, chat_id, f"🔁 Você disse: {texto}")
        return

    memoria.lembrar_fala(chat_id, "user", texto)
    instrucoes = montar_instrucoes(persona, memoria.ficha(chat_id), campos)
    ferramentas = [ferramenta_do_caderninho(campos)] if campos else []
    resposta = pensar(
        cerebro, instrucoes, memoria.historico(chat_id), ferramentas,
        lambda entrada: executar_anotacao(memoria, campos, chat_id, entrada),
    )
    memoria.lembrar_fala(chat_id, "assistant", resposta)
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

    try:
        memoria = Memoria(BANCO)
        apagadas = memoria.apagar_antigas()
    except (sqlite3.Error, OSError) as erro:
        parar(
            f"Não consegui abrir o caderninho em {BANCO} ({erro}).\n"
            "   Confira se a pasta 'dados' pode ser criada ao lado do meu_bot.py.",
            7,
        )
    if apagadas:
        pulso(f"🧹 Apaguei registros de clientes sem conversa há mais de {DIAS_PARA_ESQUECER} dias.")

    cerebro = preparar_cerebro(segredos.get("ANTHROPIC_API_KEY", ""))
    persona = ler_persona()
    campos = ler_campos_permitidos()

    terra(f"Tudo certo! Seu bot @{eu['username']} está ligado e ouvindo.")
    if cerebro:
        pulso("Modo CÉREBRO: vou responder com IA seguindo o persona.txt.")
        if campos:
            pulso(f"Caderninho ligado. Campos que posso anotar: {', '.join(campos)}.")
        else:
            pulso("Caderninho desligado: não achei campos no memoria.txt.")
    else:
        pulso("Modo ECO: vou só repetir o que receber. É o teste do encanamento.")
    faisca(f"Abra o Telegram, procure @{eu['username']} e mande um oi!")
    if sys.stdin is not None and sys.stdin.isatty():
        print("\n   (Para desligar, aperte Ctrl + C nesta janela.)\n", flush=True)

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
                responder(token, cerebro, persona, campos, memoria, mensagem)
            except ErroTelegram as erro:
                terra(f"Não consegui entregar a resposta ({erro}).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        faisca("Bot desligado. Até a próxima! 👋\n")
