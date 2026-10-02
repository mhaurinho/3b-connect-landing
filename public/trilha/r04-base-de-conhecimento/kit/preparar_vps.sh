#!/usr/bin/env bash
# preparar_vps.sh — 3B Connect, Receita 02
#
# Deixa o seu bot ligado 24 horas numa VPS com Ubuntu ou Debian.
# Rode na VPS, como root:   bash /root/meu-primeiro-bot/preparar_vps.sh
#
# É seguro rodar de novo: use o mesmo comando sempre que mudar o
# persona.txt ou o segredos.txt no seu PC e mandar a pasta outra vez.

set -euo pipefail

SERVICO="meu-bot"
USUARIO="bot"
DESTINO="/home/${USUARIO}/meu-primeiro-bot"
ORIGEM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

terra()  { printf '\n🟢 Terra: %b\n' "$1"; }
pulso()  { printf '\n🟣 Pulso: %b\n' "$1"; }
faisca() { printf '\n🟡 Faísca: %b\n' "$1"; }

falhar() {
  terra "$1"
  printf '\n   A preparação parou aqui. Corrija e rode o mesmo comando de novo:\n'
  printf '   é seguro repetir.\n\n'
  exit 1
}

trap 'falhar "Algo inesperado deu errado (linha $LINENO).\n   Copie as últimas linhas acima e mande para o suporte da 3B."' ERR

# --- Conferências antes de mexer em qualquer coisa ---------------------------

[ "$(id -u)" -eq 0 ] || falhar "Este script precisa rodar como root.\n   Rode:  sudo bash $0"

command -v apt-get >/dev/null 2>&1 || falhar "Este script é para VPS com Ubuntu ou Debian.\n   Reinstale a VPS escolhendo Ubuntu 24.04 no painel do provedor."

[ -f "$ORIGEM/meu_bot.py" ] || falhar "Não achei o meu_bot.py ao lado deste script.\n   O preparar_vps.sh precisa estar DENTRO da pasta meu-primeiro-bot."

if [ ! -f "$ORIGEM/segredos.txt" ]; then
  falhar "Não achei o segredos.txt na pasta que chegou.\n   Confira se ele existe na pasta do seu PC e mande a pasta de novo."
fi

# O Bloco de Notas pode salvar com BOM e quebras de linha do Windows (\r).
if ! tr -d '\r' < "$ORIGEM/segredos.txt" | sed 's/^\xEF\xBB\xBF//' | grep -v '^[[:space:]]*#' \
    | grep -Eq '^[[:space:]]*TELEGRAM_TOKEN[[:space:]]*=[[:space:]]*[^[:space:]]+'; then
  falhar "O segredos.txt chegou sem o TELEGRAM_TOKEN preenchido.\n   Preencha no PC, mande a pasta de novo e repita."
fi

faisca "Vamos deixar seu bot ligado 24 horas! São 5 etapas, uns 3 minutos."

# --- 1. Programas -----------------------------------------------------------

pulso "1/5 Atualizando o servidor e instalando o Python..."
export DEBIAN_FRONTEND=noninteractive
# Avisos do apt confundem quem está começando: só aparecem se der erro de verdade.
APT_LOG="$(mktemp)"
if ! { apt-get update -qq && apt-get install -y -qq python3 python3-venv; } >"$APT_LOG" 2>&1; then
  trap - ERR
  tail -n 15 "$APT_LOG"
  falhar "Não consegui instalar o Python. Confira se a VPS tem internet\n   e rode o comando de novo daqui a 1 minuto."
fi
rm -f "$APT_LOG"

# --- 2. Usuário sem poderes -------------------------------------------------

pulso "2/5 Criando o usuário '${USUARIO}', que só sabe rodar o bot..."
if ! id "$USUARIO" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${USUARIO}" --shell /usr/sbin/nologin "$USUARIO"
fi

# --- 3. Arquivos ------------------------------------------------------------

pulso "3/5 Copiando o bot para ${DESTINO}..."
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "$DESTINO"
# A pasta "dados" guarda a memória do bot (Receita 03). Ela nunca é sobrescrita.
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "$DESTINO/dados"
for arquivo in meu_bot.py persona.txt memoria.txt segredos.txt; do
  if [ -f "$ORIGEM/$arquivo" ]; then
    install -m 600 -o "$USUARIO" -g "$USUARIO" "$ORIGEM/$arquivo" "$DESTINO/$arquivo"
  fi
done
# A base de conhecimento (Receita 04) fica igualzinha à do PC: documento
# apagado no PC também some da VPS.
if [ -d "$ORIGEM/conhecimento" ]; then
  rm -rf "$DESTINO/conhecimento"
  install -d -m 700 -o "$USUARIO" -g "$USUARIO" "$DESTINO/conhecimento"
  find "$ORIGEM/conhecimento" -maxdepth 1 -type f \( -name '*.txt' -o -name '*.md' \) \
    -exec install -m 600 -o "$USUARIO" -g "$USUARIO" {} "$DESTINO/conhecimento/" \;
fi

# --- 4. Peça da IA ----------------------------------------------------------

pulso "4/5 Instalando a peça que conversa com a IA (pode levar 1 minuto)..."
if [ ! -x "$DESTINO/.venv/bin/python" ]; then
  runuser -u "$USUARIO" -- python3 -m venv "$DESTINO/.venv"
fi
PECAS="anthropic"
# A peça do Composio (Receita 05) só entra se você colocou a chave dele.
if tr -d '\r' < "$DESTINO/segredos.txt" | grep -Eq '^[[:space:]]*COMPOSIO_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]+'; then
  PECAS="anthropic composio"
fi
runuser -u "$USUARIO" -- "$DESTINO/.venv/bin/python" -m pip install -q --disable-pip-version-check $PECAS

# --- 5. Serviço -------------------------------------------------------------

pulso "5/5 Criando o serviço que liga o bot sozinho, até depois de reiniciar..."
cat > "/etc/systemd/system/${SERVICO}.service" <<EOF
# Criado pelo preparar_vps.sh (3B Connect, Receita 02).
[Unit]
Description=Meu primeiro bot (3B Connect)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${USUARIO}
WorkingDirectory=${DESTINO}
ExecStart=${DESTINO}/.venv/bin/python meu_bot.py
Environment=PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
Restart=always
RestartSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${DESTINO}/dados

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICO" >/dev/null 2>&1
INICIO="$(date +%s)"
systemctl restart "$SERVICO"

trap - ERR
# Espera o bot dizer que ligou (ou que parou), em vez de um tempo fixo:
# a primeira conversa com o Telegram pode demorar.
for _ in $(seq 1 40); do
  if journalctl -u "$SERVICO" --since "@$INICIO" -o cat --no-pager 2>/dev/null \
      | grep -qE "ligado e ouvindo|O bot parou"; then
    break
  fi
  sleep 1
done
sleep 2

if systemctl is-active --quiet "$SERVICO"; then
  terra "Tudo certo! O serviço '${SERVICO}' está rodando."
  printf '\n   Últimas falas do bot:\n\n'
  journalctl -u "$SERVICO" --since "@$INICIO" -n 12 --no-pager -o cat | grep -vE '^(Stopping|Stopped|Started|meu-bot\.service:)' | sed 's/^/   │ /' || true
  # A pasta de entrega já cumpriu seu papel. Apagar evita uma segunda cópia do
  # segredos.txt e garante que um arquivo apagado no PC não volte no próximo envio.
  if [ "$ORIGEM" != "$DESTINO" ] && [ -f "$ORIGEM/meu_bot.py" ]; then
    rm -rf "$ORIGEM"
    pulso "🧹 Apaguei a pasta de entrega ${ORIGEM}: o bot já mora em ${DESTINO}.\n   Da próxima vez, é só mandar a pasta de novo e rodar este mesmo comando."
  fi
  faisca "Agora feche esta janela, desligue o PC se quiser e mande\n   uma mensagem para o bot pelo celular. Ele continua respondendo!\n"
else
  terra "O serviço foi criado, mas o bot não ficou de pé. Veja o motivo abaixo,\n   na fala da Terra dentro do log:"
  printf '\n'
  journalctl -u "$SERVICO" --since "@$INICIO" -n 15 --no-pager -o cat | grep -vE '^(Stopping|Stopped|Started|meu-bot\.service:)' | sed 's/^/   │ /' || true
  printf '\n   Se a Terra disse "Já existe outra cópia deste bot ligada": só desligue\n'
  printf '   o bot no seu PC. O servidor religa este aqui sozinho em 15 segundos.\n'
  printf '   Nos outros casos: corrija no PC, mande a pasta de novo e rode este\n'
  printf '   script outra vez.\n\n'
  exit 1
fi
