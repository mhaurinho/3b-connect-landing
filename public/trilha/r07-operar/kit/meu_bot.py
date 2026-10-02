"""
Meu bot, pronto para operar — 3B Connect, Receita 07 (versão 5 do meu_bot.py).

Como ligar:    python meu_bot.py      (no Mac/Linux: python3 meu_bot.py)
Como desligar: aperte Ctrl + C na janela do terminal.

O que mudou desde a Receita 05:
  - O bot anota, a cada resposta, quanto a IA custou (uma estimativa com os
    preços oficiais escritos abaixo) e conta mensagens, dúvidas, travas e pedidos.
  - /relatorio (só o dono): o resumo da semana e do mês, no próprio Telegram.
  - Quando o bot liga (ou religa sozinho depois de uma queda), ele avisa o dono.
    No máximo um aviso por hora, para não virar spam se algo ficar caindo.
  - Continua tudo da Receita 05: agendamento com aprovação, base, caderninho e travas.

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
PASTA_CONHECIMENTO = PASTA / "conhecimento"
ARQUIVO_DUVIDAS = PASTA_DADOS / "duvidas.txt"

# Endereço do Telegram. Só muda em teste automatizado.
TELEGRAM_API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")

MODELO = "claude-opus-5-5"
HISTORICO_MAXIMO = 20      # mensagens recentes guardadas por conversa
DIAS_PARA_ESQUECER = 180   # ficha sem conversa por mais tempo que isso é apagada
TAMANHO_MAXIMO_ANOTACAO = 120
VOLTAS_MAXIMAS = 4         # quantas vezes seguidas a IA pode usar ferramentas
TAMANHO_MAXIMO_BASE = 200_000  # letras; acima disso a base fica cara e confusa

# Agenda. Horário de Brasília (o Brasil não tem horário de verão desde 2019).
FUSO = timezone(timedelta(hours=-3))
FUSO_NOME = "America/Sao_Paulo"
FERRAMENTA_AGENDA = "GOOGLECALENDAR_CREATE_EVENT"
VERSAO_AGENDA = "20260915_00"  # versão fixa: a ferramenta não muda sem você saber
PEDIDOS_PENDENTES_MAXIMOS = 2  # por cliente, para ninguém lotar a sua fila

# Preços do Claude Opus 5.5 em dólar por 1 milhão de tokens (tabela da Anthropic,
# setembro de 2026). Servem para ESTIMAR o gasto. O valor oficial é o do painel
# da Anthropic: confira lá e atualize aqui se os preços mudarem.
PRECO_ENTRADA = 4.00
PRECO_SAIDA = 20.00
PRECO_LEITURA_CACHE = 0.20
PRECO_ESCRITA_CACHE = 5.00   # 1,25 × entrada, para o cache de 5 minutos
AVISO_DE_LIGADO_MINUTOS = 60  # no máximo um aviso de "fui religado" por hora

# ---------------------------------------------------------------------------
# Falas dos guias no terminal
# ---------------------------------------------------------------------------

EVENTOS = None  # o banco de eventos; fica pronto quando o bot liga


def contar(tipo, valor=1.0):
    if EVENTOS is not None:
        try:
            EVENTOS.registrar(tipo, valor)
        except sqlite3.Error:
            pass  # contar nunca pode derrubar o atendimento


def terra(texto):
    if "🛡️" in texto:
        contar("trava")
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


def enviar(token, chat_id, texto, botoes=None):
    dados = {"chat_id": chat_id, "text": texto[:4000]}
    if botoes:
        dados["reply_markup"] = {"inline_keyboard": [botoes]}
    return telegram(token, "sendMessage", dados)


def editar(token, chat_id, mensagem_id, texto):
    telegram(token, "editMessageText", {"chat_id": chat_id, "message_id": mensagem_id, "text": texto[:4000]})


def responder_clique(token, clique_id, texto):
    telegram(token, "answerCallbackQuery", {"callback_query_id": clique_id, "text": texto[:190]})



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
            CREATE TABLE IF NOT EXISTS eventos (tipo TEXT, valor REAL, em TEXT);
            CREATE INDEX IF NOT EXISTS eventos_em ON eventos (em);
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, nome TEXT, servico TEXT, inicio TEXT, minutos INTEGER,
                status TEXT, criado_em TEXT);
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
            self.banco.execute("DELETE FROM pedidos WHERE chat_id = ?", (chat_id,))

    # --- contadores para o /relatorio ---
    def registrar(self, tipo, valor=1.0):
        with self.banco:
            self.banco.execute("INSERT INTO eventos VALUES (?, ?, ?)", (tipo, float(valor), self._agora()))

    def somar(self, desde):
        linhas = self.banco.execute(
            "SELECT tipo, COUNT(*), SUM(valor) FROM eventos WHERE em >= ? GROUP BY tipo",
            (desde.astimezone(timezone.utc).isoformat(timespec="seconds"),),
        )
        return {tipo: (quantos, soma or 0.0) for tipo, quantos, soma in linhas}

    def ultimo(self, tipo):
        linha = self.banco.execute("SELECT MAX(em) FROM eventos WHERE tipo = ?", (tipo,)).fetchone()
        return datetime.fromisoformat(linha[0]) if linha and linha[0] else None

    # --- pedidos de agendamento ---
    def criar_pedido(self, chat_id, nome, servico, inicio, minutos):
        with self.banco:
            cursor = self.banco.execute(
                "INSERT INTO pedidos (chat_id, nome, servico, inicio, minutos, status, criado_em) "
                "VALUES (?, ?, ?, ?, ?, 'pendente', ?)",
                (chat_id, nome, servico, inicio.isoformat(), minutos, self._agora()),
            )
            return cursor.lastrowid

    def pedido(self, numero):
        linha = self.banco.execute(
            "SELECT id, chat_id, nome, servico, inicio, minutos, status FROM pedidos WHERE id = ?",
            (numero,),
        ).fetchone()
        if not linha:
            return None
        chaves = ("id", "chat_id", "nome", "servico", "inicio", "minutos", "status")
        pedido = dict(zip(chaves, linha))
        pedido["inicio"] = datetime.fromisoformat(pedido["inicio"])
        return pedido

    def pedidos_com_status(self, status, chat_id=None):
        sql = "SELECT id FROM pedidos WHERE status = ?"
        args = [status]
        if chat_id is not None:
            sql += " AND chat_id = ?"
            args.append(chat_id)
        return [self.pedido(n) for (n,) in self.banco.execute(sql + " ORDER BY id", args)]

    def mudar_status(self, numero, de, para):
        """Muda o status só se ainda estiver como 'de'. Evita aprovar duas vezes."""
        with self.banco:
            cursor = self.banco.execute(
                "UPDATE pedidos SET status = ? WHERE id = ? AND status = ?", (para, numero, de)
            )
            return cursor.rowcount == 1

    def apagar_antigas(self):
        limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_PARA_ESQUECER)).isoformat()
        ativos = "SELECT chat_id FROM historico WHERE em >= ? UNION SELECT chat_id FROM ficha WHERE atualizado_em >= ?"
        with self.banco:
            antes = self.banco.total_changes
            self.banco.execute(f"DELETE FROM ficha WHERE chat_id NOT IN ({ativos})", (limite, limite))
            self.banco.execute(f"DELETE FROM historico WHERE chat_id NOT IN ({ativos})", (limite, limite))
            self.banco.execute("DELETE FROM pedidos WHERE criado_em < ?", (limite,))
            # Os contadores não têm dado pessoal, mas também não precisam ser eternos.
            self.banco.execute("DELETE FROM eventos WHERE em < ?", (limite,))
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
# Base de conhecimento
# ---------------------------------------------------------------------------

def carregar_conhecimento():
    """Junta os .txt e .md da pasta conhecimento/. Devolve (texto, lista de arquivos)."""
    if not PASTA_CONHECIMENTO.is_dir():
        return "", []
    partes, nomes = [], []
    for arquivo in sorted(PASTA_CONHECIMENTO.iterdir()):
        if arquivo.suffix.lower() not in (".txt", ".md") or arquivo.name.startswith("."):
            continue
        texto = arquivo.read_text(encoding="utf-8-sig").strip()
        if texto:
            partes.append(f"### Documento: {arquivo.name}\n{texto}")
            nomes.append(arquivo.name)
    return "\n\n".join(partes), nomes


def _numero(inteiro, centavos):
    valor = inteiro.replace(".", "")
    return valor if not centavos or centavos == "00" else f"{valor},{centavos}"


NUMERO = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2}))?"
PALAVRAS_DE_PRECO = r"descont|promo|off|juro|taxa|parcel|multa|acr[eé]scimo|sinal|entrada|reembols"


def valores_citados(texto):
    """Preços (R$ ou "reais"), porcentagens de preço e links que aparecem num texto."""
    precos = {"R$ " + _numero(i, c) for i, c in re.findall(r"R\$\s?" + NUMERO, texto)}
    precos |= {"R$ " + _numero(i, c) for i, c in re.findall(NUMERO + r"\s?rea(?:l|is)\b", texto, re.I)}
    # "100% tranquila" não é preço; "10% de desconto" é. Só conta % perto de palavra de preço.
    porcentagens = set()
    for achado in re.finditer(r"\d+(?:,\d+)?\s?%", texto):
        vizinhanca = texto[max(0, achado.start() - 40): achado.end() + 40].lower()
        if re.search(PALAVRAS_DE_PRECO, vizinhanca):
            porcentagens.add(achado.group(0).replace(" ", ""))
    links = {
        l.rstrip(".,;:!?)").lower()
        for l in re.findall(r"(?:https?://|www\.)\S+", texto)
    }
    return precos | porcentagens | links


def conferir_resposta(resposta, base):
    """Trava de saída: tudo o que parece preço, % ou link precisa estar na base."""
    return sorted(valores_citados(resposta) - valores_citados(base))


def registrar_duvida(pergunta):
    contar("duvida")
    pergunta, _ = mascarar(" ".join(str(pergunta).split()))
    if not pergunta:
        return False, "Recusado: dúvida vazia."
    pergunta = pergunta[:200]
    PASTA_DADOS.mkdir(mode=0o700, exist_ok=True)
    quando = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(ARQUIVO_DUVIDAS, "a", encoding="utf-8") as arquivo:
        arquivo.write(f"{quando} · {pergunta}\n")
    try:
        os.chmod(ARQUIVO_DUVIDAS, 0o600)
    except OSError:
        pass
    faisca(f"❓ Dúvida sem resposta na base, anotada em dados/duvidas.txt: {pergunta[:60]}")
    return True, "Dúvida anotada para a equipe."


FERRAMENTA_DUVIDA = {
    "name": "registrar_duvida",
    "description": (
        "Use quando o cliente perguntar algo que NÃO está respondido na BASE DE "
        "CONHECIMENTO. Escreva a dúvida de forma curta e sem dados pessoais. Depois, "
        "diga ao cliente que vai verificar com a equipe."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"pergunta": {"type": "string"}},
        "required": ["pergunta"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Agenda: o bot prepara, o dono aprova, o Composio executa
# ---------------------------------------------------------------------------

DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def agora():
    return datetime.now(FUSO)


def quando_por_extenso(inicio, minutos=None):
    texto = f"{DIAS[inicio.weekday()]}, {inicio:%d/%m/%Y} às {inicio:%H:%M}"
    if minutos:
        horas, resto = divmod(minutos, 60)
        duracao = (f"{horas}h" if horas else "") + (f"{resto:02d}min" if resto else "")
        texto += f" ({duracao})"
    return texto


FERRAMENTA_AGENDAMENTO = {
    "name": "pedir_agendamento",
    "description": (
        "Envia um PEDIDO de agendamento para a equipe aprovar. Use só quando o cliente "
        "já disse o nome, o serviço e o dia e horário que quer, dentro do horário de "
        "atendimento da base. Isto NÃO confirma nada: quem confirma é a equipe."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "nome": {"type": "string", "description": "Como o cliente quer ser chamado."},
            "servico": {"type": "string", "description": "Serviço da base, ex.: Ensaio de família."},
            "data_hora": {"type": "string", "description": "Início no formato AAAA-MM-DDTHH:MM, horário de Brasília."},
            "duracao_minutos": {"type": "integer", "description": "Duração do serviço segundo a base."},
        },
        "required": ["nome", "servico", "data_hora", "duracao_minutos"],
        "additionalProperties": False,
    },
}


def validar_pedido(entrada):
    """Confere o pedido antes de incomodar o dono. Devolve (dados, erro)."""
    nome = " ".join(str(entrada.get("nome", "")).split())[:80]
    servico = " ".join(str(entrada.get("servico", "")).split())[:80]
    if not nome or not servico:
        return None, "Faltou o nome ou o serviço. Pergunte ao cliente."
    if motivo_para_recusar(f"{nome} {servico}"):
        return None, "O pedido tinha dado sensível. Peça só o nome e o serviço."
    try:
        inicio = datetime.strptime(str(entrada.get("data_hora", ""))[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None, "Data e hora fora do formato AAAA-MM-DDTHH:MM. Confirme com o cliente."
    inicio = inicio.replace(tzinfo=FUSO)
    if inicio < agora() + timedelta(hours=1):
        return None, "Esse horário já passou ou é cedo demais. Peça outro horário ao cliente."
    if inicio > agora() + timedelta(days=180):
        return None, "Só aceitamos pedidos para os próximos 6 meses."
    try:
        minutos = int(entrada.get("duracao_minutos", 60))
    except (TypeError, ValueError):
        minutos = 60
    minutos = min(max(minutos, 15), 8 * 60)
    return {"nome": nome, "servico": servico, "inicio": inicio, "minutos": minutos}, None


def cartao_para_o_dono(pedido):
    texto = (
        f"📅 Novo pedido #{pedido['id']}\n\n"
        f"Cliente: {pedido['nome']}\n"
        f"Serviço: {pedido['servico']}\n"
        f"Quando: {quando_por_extenso(pedido['inicio'], pedido['minutos'])}\n\n"
        "Confirmar coloca na sua agenda e avisa o cliente."
    )
    botoes = [
        {"text": "✅ Confirmar", "callback_data": f"ok:{pedido['id']}"},
        {"text": "❌ Recusar", "callback_data": f"nao:{pedido['id']}"},
    ]
    return texto, botoes


def preparar_composio(chave):
    if not chave:
        return None
    try:
        from composio import Composio
    except ImportError:
        comando = "python -m pip install composio" if sys.platform.startswith("win") else ".venv/bin/python -m pip install composio"
        terra(f"Você colocou a COMPOSIO_API_KEY, mas falta a peça do Composio. Rode:\n\n       {comando}\n\n   Por enquanto, as aprovações funcionam sem criar evento na agenda.")
        return None
    return Composio(api_key=chave, toolkit_versions={"googlecalendar": VERSAO_AGENDA})


def criar_evento(composio, usuario, pedido):
    """Cria o evento no Google Agenda do dono. Devolve (ok, link do evento ou motivo do erro)."""
    fim = pedido["inicio"] + timedelta(minutes=pedido["minutos"])
    argumentos = {
        "summary": f"{pedido['servico']} — {pedido['nome']}",
        "start_datetime": pedido["inicio"].strftime("%Y-%m-%dT%H:%M:%S"),
        "end_datetime": fim.strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": FUSO_NOME,
        "description": f"Pedido #{pedido['id']} aprovado pelo dono no Telegram (bot 3B Connect).",
        "calendar_id": "primary",
    }
    try:
        resultado = composio.tools.execute(FERRAMENTA_AGENDA, argumentos, user_id=usuario)
    except Exception as erro:  # o SDK tem muitas classes de erro; todas viram aviso ao dono
        return False, erro.__class__.__name__
    if not resultado.get("successful"):
        return False, str(resultado.get("error") or "erro desconhecido")[:120]
    dados = resultado.get("data") or {}
    evento = dados.get("response_data", dados) if isinstance(dados, dict) else {}
    link = evento.get("htmlLink", "") if isinstance(evento, dict) else ""
    return True, link if str(link).startswith("https://") else ""


PROMESSA_DE_CONFIRMACAO = re.compile(
    r"\b(est[aá]|fica|ficou|foi|j[aá] est[aá]|t[aá])\s+(confirmad|agendad|marcad|reservad)"
    r"|\b(agendamento|reserva|hor[aá]rio)\s+(confirmad|garantid)"
    # Frases de "fechar negócio" que um cliente pode tentar arrancar do bot
    # (como no caso real da concessionária que "vendeu" um carro por 1 dólar).
    r"|\bneg[oó]cio fechado|\bfechado por\b|\bvalor legal\b|\bjuridicamente\b|\blegalmente v[aá]lid",
    re.IGNORECASE,
)


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


def montar_instrucoes(persona, base, ficha, campos, agenda_ligada=False):
    """Duas partes: a fixa (persona + base, guardada em cache) e a do cliente."""
    if base:
        regras_base = (
            "BASE DE CONHECIMENTO\n"
            "Responda SOMENTE com o que está nos documentos abaixo. Não complete com o "
            "que você sabe de fora. Nunca invente preço, prazo, desconto, endereço, "
            "horário ou link. Se a resposta não estiver aqui, use a ferramenta "
            "registrar_duvida e diga que vai verificar com a equipe.\n\n" + base
        )
    else:
        regras_base = (
            "BASE DE CONHECIMENTO\n(vazia) Se perguntarem algo que não está na persona, "
            "use registrar_duvida e diga que vai verificar com a equipe."
        )
    fixa = f"{persona}\n\n---\n{regras_base}"

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
    if agenda_ligada:
        agendamento = (
            "AGENDAMENTO\n"
            "Quando o cliente quiser marcar, descubra o nome, o serviço e o dia e horário "
            "desejados, dentro do horário de atendimento da base. Então use pedir_agendamento "
            "com a duração do serviço que está na base. Você NUNCA confirma: diga que o pedido "
            "foi para a equipe e que a confirmação chega aqui mesmo, nesta conversa."
        )
    else:
        agendamento = (
            "AGENDAMENTO\nVocê não marca horários. Se o cliente quiser agendar, anote o "
            "interesse e diga que a equipe entra em contato."
        )
    momento = f"AGORA: {quando_por_extenso(agora())} (horário de Brasília)."
    do_cliente = f"{caderninho}\n\n{agendamento}\n\n{momento}\n\nFICHA DESTE CLIENTE:\n{sabido}"

    # A parte fixa vem primeiro e é marcada para cache: a Anthropic reaproveita
    # o processamento dela entre mensagens, o que deixa cada resposta mais barata.
    return [
        {"type": "text", "text": fixa, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": do_cliente},
    ]


def custo_em_dolar(uso):
    """Estima o custo de UMA chamada à IA a partir do 'usage' que ela devolve."""
    if uso is None:
        return 0.0
    def n(campo):
        return getattr(uso, campo, 0) or 0
    return (
        n("input_tokens") * PRECO_ENTRADA
        + n("output_tokens") * PRECO_SAIDA
        + n("cache_read_input_tokens") * PRECO_LEITURA_CACHE
        + n("cache_creation_input_tokens") * PRECO_ESCRITA_CACHE
    ) / 1_000_000


def pensar(cliente, instrucoes, historico, ferramentas, executar):
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
            contar("custo_usd", custo_em_dolar(getattr(resposta, "usage", None)))
            if resposta.stop_reason != "tool_use":
                break
            # A IA quer usar uma ferramenta: devolvemos o pedido dela e o resultado.
            mensagens.append({"role": "assistant", "content": resposta.content})
            resultados = []
            for bloco in resposta.content:
                if bloco.type == "tool_use":
                    ok, texto = executar(bloco.name, bloco.input)
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

RESPOSTA_SEGURA = (
    "Essa informação eu prefiro confirmar com a equipe antes de te passar, tá? "
    "Já deixei anotado para te responderem. 😊"
)
RESPOSTA_SEM_CONFIRMAR = (
    "Seu pedido está com a equipe, e a confirmação chega aqui mesmo, nesta conversa. 😊"
)


class Config:
    """O que o bot precisa saber para conversar e para pedir aprovação."""

    def __init__(self, token, cerebro, persona, base, campos, dono, composio, usuario_composio):
        self.token = token
        self.cerebro = cerebro
        self.persona = persona
        self.base = base
        self.campos = campos
        self.dono = dono
        self.composio = composio
        self.usuario_composio = usuario_composio


def mostrar_ficha(ficha):
    if not ficha:
        return "Não tenho nenhuma anotação sobre você. 🙂"
    linhas = "\n".join(f"• {c}: {v}" for c, v in ficha.items())
    return f"Isto é tudo o que anotei sobre você:\n{linhas}\n\nPara apagar tudo, envie /esquecer."


def montar_relatorio(memoria):
    hoje = agora()
    semana = memoria.somar(hoje - timedelta(days=7))
    mes = memoria.somar(hoje.replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    def linha(rotulo, tipo, dados):
        return f"{rotulo}: {dados.get(tipo, (0, 0))[0]}"

    def bloco(titulo, dados):
        gasto = dados.get("custo_usd", (0, 0.0))[1]
        return "\n".join([
            titulo,
            linha("💬 Mensagens de clientes", "mensagem", dados),
            linha("❓ Dúvidas sem resposta", "duvida", dados),
            linha("🛡️ Travas acionadas", "trava", dados),
            linha("📅 Pedidos recebidos", "pedido", dados),
            linha("✅ Pedidos confirmados", "confirmado", dados),
            f"💵 Gasto estimado com a IA: US$ {gasto:.2f}",
        ])

    pendentes = len(memoria.pedidos_com_status("pendente"))
    return (
        f"📊 Relatório do bot · {hoje:%d/%m/%Y %H:%M}\n\n"
        + bloco("ÚLTIMOS 7 DIAS", semana) + "\n\n"
        + bloco(f"DESDE 01/{hoje:%m}", mes) + "\n\n"
        + f"⏳ Pedidos esperando você agora: {pendentes}\n\n"
        + "O gasto é uma estimativa. O valor oficial está no painel da Anthropic."
    )


def avisar_que_liguei(cfg, memoria):
    """Avisa o dono que o bot ligou, no máximo uma vez por hora."""
    if cfg.dono is None:
        return
    anterior = memoria.ultimo("ligado")
    memoria.registrar("ligado")
    if anterior and agora() - anterior < timedelta(minutes=AVISO_DE_LIGADO_MINUTOS):
        return
    try:
        enviar(cfg.token, cfg.dono, f"🟢 Seu bot foi ligado às {agora():%H:%M} de {agora():%d/%m}. Se você não religou nada, o servidor pode ter reiniciado: está tudo certo, ele voltou sozinho.\n\nResumo: /relatorio")
    except ErroTelegram:
        pass


def avisar_dono(cfg, memoria, numero):
    texto, botoes = cartao_para_o_dono(memoria.pedido(numero))
    enviar(cfg.token, cfg.dono, texto, botoes)


def responder(cfg, memoria, mensagem):
    chat_id = mensagem["chat"]["id"]
    nome = mensagem.get("from", {}).get("first_name", "você")
    texto = mensagem.get("text")
    token = cfg.token

    if texto is None:
        enviar(token, chat_id, "Por enquanto eu só entendo mensagens de texto. 🙂")
        return

    texto, mascarou = mascarar(texto)
    print(f"   📩 {nome} mandou: {texto[:60]}", flush=True)
    if mascarou:
        terra("🛡️ Trava: a mensagem tinha um número sensível. Mascarei antes de guardar.")

    if texto.startswith("/start"):
        modo = "com cérebro de IA" if cfg.cerebro else "no modo ECO (só repito)"
        aviso = (
            "\n\nPara te atender melhor, eu guardo algumas anotações sobre a nossa conversa. "
            "Veja com /minhaficha e apague tudo quando quiser com /esquecer."
            if cfg.cerebro and cfg.campos else ""
        )
        enviar(token, chat_id, f"Oi, {nome}! Estou ligado {modo}.{aviso}")
        return

    if texto.startswith("/meuid"):
        enviar(token, chat_id, f"O seu número de conversa é: {chat_id}\n\nSe você é o dono do bot, cole no segredos.txt:\nDONO_CHAT_ID={chat_id}")
        return

    if texto.startswith("/relatorio"):
        if chat_id != cfg.dono:
            enviar(token, chat_id, "Esse comando é só para a equipe. 🙂")
            return
        enviar(token, chat_id, montar_relatorio(memoria))
        return

    if texto.startswith("/pedidos"):
        if chat_id != cfg.dono:
            enviar(token, chat_id, "Esse comando é só para a equipe. 🙂")
            return
        pendentes = memoria.pedidos_com_status("pendente")
        if not pendentes:
            enviar(token, chat_id, "Nenhum pedido esperando aprovação. ✨")
        for pedido in pendentes:
            avisar_dono(cfg, memoria, pedido["id"])
        return

    if texto.startswith("/minhaficha"):
        enviar(token, chat_id, mostrar_ficha(memoria.ficha(chat_id)))
        return

    if texto.startswith("/esquecer"):
        memoria.esquecer(chat_id)
        pulso("🧹 Um cliente pediu /esquecer: ficha, conversa e pedidos apagados.")
        enviar(token, chat_id, "Pronto, apaguei tudo o que eu sabia sobre você e a nossa conversa.")
        return

    if not cfg.cerebro:
        enviar(token, chat_id, f"🔁 Você disse: {texto}")
        return

    contar("mensagem")

    agenda_ligada = cfg.dono is not None

    def executar(nome_ferramenta, entrada):
        if nome_ferramenta == "anotar_na_ficha" and cfg.campos:
            return executar_anotacao(memoria, cfg.campos, chat_id, entrada)
        if nome_ferramenta == "registrar_duvida":
            return registrar_duvida(entrada.get("pergunta", ""))
        if nome_ferramenta == "pedir_agendamento" and agenda_ligada:
            if len(memoria.pedidos_com_status("pendente", chat_id)) >= PEDIDOS_PENDENTES_MAXIMOS:
                return False, "Este cliente já tem pedidos esperando a equipe. Diga que a equipe responde em breve."
            dados, erro = validar_pedido(entrada)
            if erro:
                return False, erro
            numero = memoria.criar_pedido(chat_id, **dados)
            try:
                avisar_dono(cfg, memoria, numero)
            except ErroTelegram:
                memoria.mudar_status(numero, "pendente", "erro")
                return False, "Não consegui avisar a equipe agora. Diga que a equipe entra em contato."
            contar("pedido")
            pulso(f"📅 Pedido #{numero} enviado para você aprovar no Telegram.")
            return True, (
                f"Pedido #{numero} enviado para a equipe aprovar. Diga ao cliente que a "
                "confirmação chega aqui mesmo. NÃO diga que está confirmado."
            )
        return False, f"Ferramenta desconhecida: {nome_ferramenta}"

    memoria.lembrar_fala(chat_id, "user", texto)
    instrucoes = montar_instrucoes(cfg.persona, cfg.base, memoria.ficha(chat_id), cfg.campos, agenda_ligada)
    ferramentas = [FERRAMENTA_DUVIDA]
    if cfg.campos:
        ferramentas.insert(0, ferramenta_do_caderninho(cfg.campos))
    if agenda_ligada:
        ferramentas.append(FERRAMENTA_AGENDAMENTO)
    resposta = pensar(cfg.cerebro, instrucoes, memoria.historico(chat_id), ferramentas, executar)

    inventado = conferir_resposta(resposta, cfg.persona + "\n" + cfg.base)
    if inventado:
        terra(
            f"🛡️ Trava de saída: a resposta citava {', '.join(inventado)}, "
            f"{'que não está' if len(inventado) == 1 else 'que não estão'} na base.\n"
            "   Mandei uma resposta segura no lugar e anotei a dúvida."
        )
        registrar_duvida(f"(a IA tentou citar {', '.join(inventado)}) {texto}")
        resposta = RESPOSTA_SEGURA
    elif PROMESSA_DE_CONFIRMACAO.search(resposta) and not memoria.pedidos_com_status("confirmado", chat_id):
        terra("🛡️ Trava de saída: a IA ia dizer que está confirmado, mas você não aprovou nada.\n   Troquei a frase.")
        resposta = RESPOSTA_SEM_CONFIRMAR

    memoria.lembrar_fala(chat_id, "assistant", resposta)
    enviar(token, chat_id, resposta)
    print(f"   💬 Bot respondeu: {resposta[:60]}", flush=True)


def tratar_clique(cfg, memoria, clique):
    """O dono apertou ✅ ou ❌ num cartão de pedido."""
    token = cfg.token
    quem = clique.get("from", {}).get("id")
    mensagem = clique.get("message") or {}
    acao, _, numero = str(clique.get("data", "")).partition(":")

    if cfg.dono is None or quem != cfg.dono:
        terra("🛡️ Trava: alguém que não é o dono tentou aprovar um pedido. Ignorei.")
        responder_clique(token, clique["id"], "Só a equipe pode aprovar pedidos.")
        return
    pedido = memoria.pedido(int(numero)) if numero.isdigit() else None
    if not pedido or pedido["status"] != "pendente":
        responder_clique(token, clique["id"], "Esse pedido já foi resolvido.")
        return

    quando = quando_por_extenso(pedido["inicio"])
    if acao == "nao":
        memoria.mudar_status(pedido["id"], "pendente", "recusado")
        editar(token, cfg.dono, mensagem.get("message_id"), f"❌ Pedido #{pedido['id']} recusado.\n{pedido['servico']} — {pedido['nome']}, {quando}")
        aviso = f"A equipe não conseguiu confirmar {quando}. Quer sugerir outro dia ou horário? 🙂"
        enviar(token, pedido["chat_id"], aviso)
        memoria.lembrar_fala(pedido["chat_id"], "assistant", aviso)
        responder_clique(token, clique["id"], "Recusado. O cliente foi avisado.")
        pulso(f"❌ Você recusou o pedido #{pedido['id']}.")
        return
    if acao != "ok":
        responder_clique(token, clique["id"], "Botão desconhecido.")
        return

    # Trava contra clique duplo: só um clique muda de "pendente" para "confirmando".
    if not memoria.mudar_status(pedido["id"], "pendente", "confirmando"):
        responder_clique(token, clique["id"], "Esse pedido já está sendo resolvido.")
        return
    nota = ""
    if cfg.composio:
        ok, detalhe = criar_evento(cfg.composio, cfg.usuario_composio, pedido)
        if not ok:
            memoria.mudar_status(pedido["id"], "confirmando", "pendente")
            terra(f"Não consegui criar o evento na agenda ({detalhe}). O pedido #{pedido['id']} continua esperando.")
            editar(token, cfg.dono, mensagem.get("message_id"), f"⚠️ Pedido #{pedido['id']}: não consegui criar o evento na agenda ({detalhe}).\nConfira a conexão (rode o conectar_agenda.py) e use /pedidos para tentar de novo.")
            responder_clique(token, clique["id"], "Falhou ao criar na agenda.")
            return
        nota = "\n📆 Já está na sua agenda." + (f"\n{detalhe}" if detalhe else "")
    else:
        nota = "\n📝 Agenda não conectada: anote na sua agenda."
    memoria.mudar_status(pedido["id"], "confirmando", "confirmado")
    contar("confirmado")
    editar(token, cfg.dono, mensagem.get("message_id"), f"✅ Pedido #{pedido['id']} confirmado.\n{pedido['servico']} — {pedido['nome']}, {quando}{nota}")
    aviso = f"✅ Confirmado! {pedido['servico']}: {quando}. Até lá! 😊"
    enviar(token, pedido["chat_id"], aviso)
    memoria.lembrar_fala(pedido["chat_id"], "assistant", aviso)
    responder_clique(token, clique["id"], "Confirmado! O cliente foi avisado.")
    pulso(f"✅ Você confirmou o pedido #{pedido['id']}.{' Evento criado no Google Agenda.' if cfg.composio else ''}")


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
    try:
        base, documentos = carregar_conhecimento()
    except (OSError, UnicodeDecodeError) as erro:
        parar(
            f"Não consegui ler a pasta conhecimento ({erro}).\n"
            "   Salve os documentos como texto (.txt ou .md), em UTF-8.",
            9,
        )
    if len(base) > TAMANHO_MAXIMO_BASE:
        parar(
            f"A base de conhecimento tem {len(base)} letras, acima do limite de {TAMANHO_MAXIMO_BASE}.\n"
            "   Resuma os documentos: base grande demais fica cara e confunde o bot.",
            9,
        )

    dono_texto = segredos.get("DONO_CHAT_ID", "")
    dono = int(dono_texto) if re.fullmatch(r"-?\d+", dono_texto or "") else None
    if dono_texto and dono is None:
        parar("O DONO_CHAT_ID no segredos.txt precisa ser só números. Mande /meuid para o bot e copie.", 2)
    composio = preparar_composio(segredos.get("COMPOSIO_API_KEY", "")) if dono else None
    cfg = Config(token, cerebro, persona, base, campos, dono, composio,
                 segredos.get("COMPOSIO_USER_ID", "") or "dono")
    global EVENTOS
    EVENTOS = memoria
    avisar_que_liguei(cfg, memoria)

    terra(f"Tudo certo! Seu bot @{eu['username']} está ligado e ouvindo.")
    if cerebro:
        pulso("Modo CÉREBRO: vou responder com IA seguindo o persona.txt.")
        if documentos:
            pulso(f"Base de conhecimento: {len(documentos)} documento(s): {', '.join(documentos)}.")
        else:
            pulso("Base de conhecimento vazia: crie a pasta 'conhecimento' com seus documentos.")
        if campos:
            pulso(f"Caderninho ligado. Campos que posso anotar: {', '.join(campos)}.")
        else:
            pulso("Caderninho desligado: não achei campos no memoria.txt.")
        if dono and composio:
            pulso("Agendamento ligado: pedidos vão para você aprovar, e o aprovado vai para o Google Agenda.")
        elif dono:
            pulso("Agendamento ligado: pedidos vão para você aprovar (sem Google Agenda: falta COMPOSIO_API_KEY).")
        else:
            pulso("Agendamento desligado: falta DONO_CHAT_ID no segredos.txt (mande /meuid para descobrir).")
    else:
        pulso("Modo ECO: vou só repetir o que receber. É o teste do encanamento.")
    faisca(f"Abra o Telegram, procure @{eu['username']} e mande um oi!")
    if sys.stdin is not None and sys.stdin.isatty():
        print("\n   (Para desligar, aperte Ctrl + C nesta janela.)\n", flush=True)

    proxima = None
    falhas_seguidas = 0
    while True:
        try:
            pedido = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
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
            try:
                if novidade.get("callback_query"):
                    tratar_clique(cfg, memoria, novidade["callback_query"])
                elif novidade.get("message"):
                    responder(cfg, memoria, novidade["message"])
            except ErroTelegram as erro:
                terra(f"Não consegui entregar a resposta ({erro}).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        faisca("Bot desligado. Até a próxima! 👋\n")
