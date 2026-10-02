"""
Conectar a agenda — 3B Connect, Receita 05.

Liga o SEU Google Agenda ao Composio, para o bot poder criar eventos
depois que você aprovar. Rode uma vez só, no PC:

    python conectar_agenda.py        (no Mac/Linux: .venv/bin/python conectar_agenda.py)

Ele mostra um link. Você abre, entra com a sua conta Google e autoriza.
O bot nunca vê a sua senha do Google: quem guarda a autorização é o Composio.
"""

import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO_SEGREDOS = PASTA / "segredos.txt"
TOOLKIT = "googlecalendar"
USUARIO_PADRAO = "dono"


def terra(texto):
    print(f"\n🟢 Terra: {texto}", flush=True)


def pulso(texto):
    print(f"\n🟣 Pulso: {texto}", flush=True)


def faisca(texto):
    print(f"\n🟡 Faísca: {texto}", flush=True)


def parar(texto):
    terra(texto)
    print("\n   Nada foi conectado. Corrija e rode de novo.\n", flush=True)
    sys.exit(1)


def ler_segredos():
    if not ARQUIVO_SEGREDOS.exists():
        parar("Não encontrei o segredos.txt nesta pasta.")
    valores = {}
    for linha in ARQUIVO_SEGREDOS.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, valor = linha.split("=", 1)
            valores[chave.strip().upper()] = valor.strip().strip('"').strip("'").strip()
    return valores


def main():
    segredos = ler_segredos()
    chave = segredos.get("COMPOSIO_API_KEY", "")
    usuario = segredos.get("COMPOSIO_USER_ID", "") or USUARIO_PADRAO
    if not chave:
        parar(
            "Falta a linha COMPOSIO_API_KEY= no segredos.txt.\n"
            "   Pegue a chave no painel do Composio (Settings → API Keys) e cole lá."
        )

    try:
        from composio import Composio
        from composio import exceptions as erros
    except ImportError:
        if sys.platform.startswith("win"):
            comando = "python -m pip install composio"
        else:
            comando = ".venv/bin/python -m pip install composio"
        parar(f"Falta instalar a peça do Composio. Rode:\n\n       {comando}")

    composio = Composio(api_key=chave)

    pulso("Procurando a configuração do Google Agenda na sua conta Composio...")
    try:
        configs = composio.auth_configs.list(toolkit_slug=TOOLKIT).items
        if configs:
            config_id = sorted(configs, key=lambda c: str(c.created_at), reverse=True)[0].id
        else:
            pulso("Não havia nenhuma. Criando uma com a autenticação gerenciada pelo Composio...")
            config_id = composio.auth_configs.create(
                TOOLKIT, {"type": "use_composio_managed_auth"}
            ).id

        ativas = composio.connected_accounts.list(
            toolkit_slugs=[TOOLKIT], user_ids=[usuario], statuses=["ACTIVE"]
        ).items
        if ativas:
            terra(f"A agenda já está conectada para o usuário '{usuario}'. Nada a fazer! ✅")
            return

        pedido = composio.connected_accounts.link(usuario, config_id)
    except erros.ComposioError as erro:
        parar(
            f"O Composio recusou o pedido ({erro.__class__.__name__}).\n"
            "   Confira se a COMPOSIO_API_KEY foi copiada inteira."
        )

    faisca(
        "Abra este link no navegador, entre com a conta Google da SUA agenda\n"
        "   e clique em permitir:\n\n"
        f"   {pedido.redirect_url}\n"
    )
    pulso("Esperando você autorizar (até 5 minutos)...")
    try:
        pedido.wait_for_connection(timeout=300)
    except erros.ComposioError:
        parar("Não vi a autorização em 5 minutos. Rode de novo e abra o link novo.")

    terra(f"Agenda conectada para o usuário '{usuario}'! ✅\n   Agora o bot pode criar eventos, sempre depois da sua aprovação.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        faisca("Conexão cancelada. Pode rodar de novo quando quiser.\n")
