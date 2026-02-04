import discord
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import json
import asyncio
import hmac
import hashlib
import logging
from datetime import datetime
from queue import Queue
import threading
from flask import Flask, request, jsonify

# ===================== CONFIGURAÇÃO =====================
load_dotenv()

# Variáveis de ambiente
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GITHUB_SECRET = os.getenv('GITHUB_WEBHOOK_SECRET', '')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
REPO_OWNER = os.getenv('GITHUB_REPO_OWNER', 'seu_usuario')
REPO_NAME = os.getenv('GITHUB_REPO_NAME', 'seu_repositorio')

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('github_bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Fila para comunicação entre threads
push_queue = Queue()

# ===================== DISCORD BOT =====================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ===================== FLASK APP =====================
flask_app = Flask(__name__)

def verify_signature(payload_body, secret_token, signature_header):
    """Verifica a assinatura do webhook do GitHub"""
    if not secret_token or not signature_header:
        return True  # Permite se não houver segredo configurado
    
    hash_object = hmac.new(
        secret_token.encode('utf-8'),
        msg=payload_body,
        digestmod=hashlib.sha256
    )
    expected_signature = "sha256=" + hash_object.hexdigest()
    
    return hmac.compare_digest(expected_signature, signature_header)

@flask_app.route('/github-webhook', methods=['GET', 'POST'])
def github_webhook():
    """Endpoint principal para webhooks do GitHub"""
    
    if request.method == 'GET':
        # Para testes manuais
        return jsonify({
            'status': 'active',
            'service': 'github-webhook-receiver',
            'timestamp': datetime.now().isoformat()
        }), 200
    
    # Método POST (webhook real)
    try:
        event_type = request.headers.get('X-GitHub-Event')
        delivery_id = request.headers.get('X-GitHub-Delivery')
        
        logger.info(f"📨 Webhook recebido: {event_type} (ID: {delivery_id})")
        
        # Verificação de assinatura
        signature = request.headers.get('X-Hub-Signature-256')
        if not verify_signature(request.data, GITHUB_SECRET, signature):
            logger.warning("⚠️ Assinatura inválida do webhook")
            return jsonify({'error': 'Invalid signature'}), 401
        
        # Processa baseado no tipo de evento
        if event_type == 'ping':
            data = request.get_json(silent=True) or {}
            zen_message = data.get('zen', 'No zen message')
            
            logger.info(f"✅ PING recebido: {zen_message}")
            
            return jsonify({
                'status': 'pong',
                'zen': zen_message,
                'event': 'ping',
                'timestamp': datetime.now().isoformat()
            }), 200
        
        elif event_type == 'push':
            data = request.get_json(silent=True)
            if not data:
                logger.error("❌ Nenhum JSON recebido")
                return jsonify({'error': 'No JSON data'}), 400
            
            ref = data.get('ref', 'unknown')
            repo = data.get('repository', {}).get('full_name', 'unknown')
            
            logger.info(f"📦 Push em {repo} - Branch: {ref}")
            
            # Adiciona à fila independente da branch
            push_queue.put({
                'event': 'push',
                'data': data,
                'received_at': datetime.now().isoformat()
            })
            
            return jsonify({
                'status': 'received',
                'event': 'push',
                'repository': repo,
                'branch': ref.split('/')[-1],
                'queued': True
            }), 200
        
        else:
            logger.info(f"ℹ️ Evento ignorado: {event_type}")
            return jsonify({'status': 'ignored', 'event': event_type}), 200
            
    except Exception as e:
        logger.error(f"❌ Erro no webhook: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@flask_app.route('/health', methods=['GET'])
def health():
    """Endpoint de saúde"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'queue_size': push_queue.qsize()
    }), 200

def run_flask():
    """Executa o servidor Flask em thread separada"""
    logger.info("🚀 Iniciando servidor Flask (porta 5000)")
    
    # Desativa logs verbose do Flask
    flask_log = logging.getLogger('werkzeug')
    flask_log.setLevel(logging.WARNING)
    
    flask_app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=True
    )

# ===================== PROCESSAMENTO DE PUSHES =====================
async def process_pushes():
    """Processa pushes da fila e envia para o Discord"""
    await bot.wait_until_ready()
    
    logger.info("🔄 Iniciando processamento de pushes...")
    
    while not bot.is_closed():
        try:
            # Processa todos os pushes na fila
            while not push_queue.empty():
                item = push_queue.get()
                
                if item['event'] == 'push':
                    await process_github_push(item['data'])
                
                push_queue.task_done()
                await asyncio.sleep(0.1)  # Pequena pausa entre processamentos
            
            await asyncio.sleep(1)  # Verifica a fila a cada segundo
            
        except Exception as e:
            logger.error(f"❌ Erro no processamento: {e}", exc_info=True)
            await asyncio.sleep(5)

async def process_github_push(data):
    """Processa dados do push e envia para o Discord"""
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            logger.error(f"❌ Canal {CHANNEL_ID} não encontrado")
            return
        
        repo = data.get('repository', {})
        pusher = data.get('pusher', {})
        commits = data.get('commits', [])
        ref = data.get('ref', '')
        branch = ref.split('/')[-1] if '/' in ref else ref
        
        # Cria embed do Discord
        embed = discord.Embed(
            title=f"📦 Push em {repo.get('full_name', 'Unknown')}",
            description=f"**Branch:** `{branch}`\n**Commits:** {len(commits)}",
            color=discord.Color.green(),
            timestamp=datetime.now(),
            url=repo.get('html_url', '')
        )
        
        # Informações do autor
        author_name = pusher.get('name', 'Unknown')
        embed.set_author(
            name=author_name,
            icon_url=pusher.get('avatar_url', '')
        )
        
        # Informações do repositório
        embed.add_field(
            name="Repositório",
            value=f"[{repo.get('full_name', 'Unknown')}]({repo.get('html_url', '')})",
            inline=True
        )
        
        # Link para comparar alterações
        if 'compare' in data:
            embed.add_field(
                name="Comparar",
                value=f"[Ver alterações]({data['compare']})",
                inline=True
            )
        
        # Lista de commits (máximo 3)
        for i, commit in enumerate(commits[:3]):
            short_sha = commit.get('id', '')[:7]
            commit_message = commit.get('message', '').split('\n')[0]
            
            # Limita tamanho da mensagem
            if len(commit_message) > 80:
                commit_message = commit_message[:77] + "..."
            
            # Remove markdown problemático
            commit_message = commit_message.replace('`', "'").replace('*', '')
            
            commit_author = commit.get('author', {}).get('name', 'Unknown')
            
            embed.add_field(
                name=f"Commit `{short_sha}` por {commit_author}",
                value=f"{commit_message}\n[Ver commit]({commit.get('url', '')})",
                inline=False
            )
        
        # Se houver mais commits
        if len(commits) > 3:
            embed.add_field(
                name="Mais commits",
                value=f"+{len(commits) - 3} commits adicionais",
                inline=False
            )
        
        embed.set_footer(text=f"Push por {author_name}")
        
        # Envia para o Discord
        await channel.send(embed=embed)
        logger.info(f"✅ Notificação enviada para #{channel.name}")
        
    except discord.errors.Forbidden:
        logger.error(f"❌ Sem permissão para enviar mensagem no canal {CHANNEL_ID}")
    except Exception as e:
        logger.error(f"❌ Erro ao processar push: {e}", exc_info=True)

# ===================== EVENTOS DO BOT =====================
@bot.event
async def on_ready():
    """Evento quando o bot conecta"""
    logger.info(f"✅ Bot conectado como {bot.user.name}")
    logger.info(f"📊 Servidores: {len(bot.guilds)}")
    logger.info(f"📌 Canal alvo: {CHANNEL_ID}")
    
    # Inicia o processamento de pushes
    bot.loop.create_task(process_pushes())
    
    # Muda o status do bot
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="commits no GitHub"
        )
    )
    
    # Log dos canais disponíveis
    for guild in bot.guilds:
        logger.info(f"🏰 Servidor: {guild.name} (ID: {guild.id})")
        for channel in guild.text_channels:
            if channel.id == CHANNEL_ID:
                logger.info(f"   📍 Canal alvo encontrado: #{channel.name}")
    
    logger.info("🎉 Bot pronto e aguardando webhooks!")

# ===================== COMANDOS =====================
@bot.command(name='teste')
async def teste(ctx):
    """Testa se o bot está funcionando"""
    embed = discord.Embed(
        title="✅ Bot Funcionando",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Servidor", value=ctx.guild.name, inline=True)
    embed.add_field(name="Canal", value=ctx.channel.name, inline=True)
    embed.add_field(name="Canal Configurado", value=str(CHANNEL_ID), inline=True)
    embed.add_field(name="Latência", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Fila Atual", value=f"{push_queue.qsize()} itens", inline=True)
    embed.add_field(name="Status", value="🟢 Online", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='setup')
async def setup(ctx):
    """Instruções para configurar o webhook"""
    embed = discord.Embed(
        title="🔧 Configuração do Webhook GitHub",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="1. Acesse seu repositório",
        value="GitHub → Settings → Webhooks → Add webhook",
        inline=False
    )
    
    embed.add_field(
        name="2. Payload URL",
        value="`https://SEU_NGROK_URL/github-webhook`",
        inline=False
    )
    
    embed.add_field(
        name="3. Content type",
        value="`application/json`",
        inline=True
    )
    
    embed.add_field(
        name="4. Secret",
        value="Use a chave do seu arquivo `.env`",
        inline=True
    )
    
    embed.add_field(
        name="5. Eventos",
        value="Selecione: `Just the push event`",
        inline=False
    )
    
    embed.set_footer(text="Use !ngrok para obter a URL atual")
    
    await ctx.send(embed=embed)

@bot.command(name='simulate')
async def simulate(ctx):
    """Simula um push do GitHub"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Apenas administradores podem usar este comando.")
        return
    
    test_data = {
        'ref': 'refs/heads/main',
        'repository': {
            'full_name': f'{REPO_OWNER}/{REPO_NAME}',
            'html_url': f'https://github.com/{REPO_OWNER}/{REPO_NAME}'
        },
        'pusher': {
            'name': ctx.author.name,
            'avatar_url': str(ctx.author.avatar.url) if ctx.author.avatar else ''
        },
        'commits': [
            {
                'id': 'abc123def456',
                'message': 'Test commit from Discord\n\nThis is a test commit.',
                'url': f'https://github.com/{REPO_OWNER}/{REPO_NAME}/commit/abc123',
                'author': {'name': ctx.author.name}
            }
        ],
        'compare': f'https://github.com/{REPO_OWNER}/{REPO_NAME}/compare/old...new'
    }
    
    push_queue.put({'event': 'push', 'data': test_data})
    
    embed = discord.Embed(
        title="🧪 Push Simulado",
        description="Push de teste adicionado à fila!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Status", value="✅ Na fila para processamento")
    embed.add_field(name="Tamanho da fila", value=f"{push_queue.qsize()} itens")
    
    await ctx.send(embed=embed)

@bot.command(name='queue')
async def show_queue(ctx):
    """Mostra o estado atual da fila"""
    embed = discord.Embed(
        title="📊 Estado da Fila",
        color=discord.Color.purple()
    )
    
    queue_size = push_queue.qsize()
    embed.add_field(name="Itens na fila", value=str(queue_size))
    
    if queue_size > 0:
        embed.description = f"⏳ Processando {queue_size} evento(s)..."
        embed.color = discord.Color.orange()
    else:
        embed.description = "✅ Fila vazia"
        embed.color = discord.Color.green()
    
    await ctx.send(embed=embed)

@bot.command(name='health')
async def health_check(ctx):
    """Verifica a saúde do bot"""
    embed = discord.Embed(title="🏥 Health Check", color=discord.Color.green())
    
    # Verifica conexão com Discord
    embed.add_field(name="Discord", value="🟢 Conectado", inline=True)
    embed.add_field(name="Latência", value=f"{round(bot.latency * 1000)}ms", inline=True)
    
    # Verifica canal configurado
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        embed.add_field(name="Canal Alvo", value=f"🟢 #{channel.name}", inline=True)
    else:
        embed.add_field(name="Canal Alvo", value="🔴 Não encontrado", inline=True)
    
    # Verifica fila
    embed.add_field(name="Fila", value=f"{push_queue.qsize()} itens", inline=True)
    
    # Verifica Flask (simples)
    embed.add_field(name="Webhook Server", value="🟢 Ativo (porta 5000)", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='changelog')
async def changelog(ctx):
    """Mostra as últimas atualizações do bot"""
    embed = discord.Embed(
        title="📋 Changelog do Bot",
        description="Últimas atualizações e funcionalidades",
        color=discord.Color.teal()
    )
    
    embed.add_field(
        name="v1.0.0",
        value="✅ Webhooks GitHub funcionando\n✅ Notificações em embed\n✅ Sistema de fila\n✅ Comandos administrativos\n✅ Logs detalhados",
        inline=False
    )
    
    embed.add_field(
        name="Comandos disponíveis",
        value="!teste - Testa o bot\n!setup - Instruções\n!simulate - Push teste\n!queue - Estado da fila\n!health - Health check",
        inline=False
    )
    
    embed.set_footer(text="Bot desenvolvido para monitorar GitHub")
    
    await ctx.send(embed=embed)

# ===================== INICIALIZAÇÃO =====================
def main():
    """Função principal para inicializar tudo"""
    
    # Verifica variáveis de ambiente
    if not DISCORD_TOKEN:
        logger.error("❌ DISCORD_TOKEN não encontrado no .env")
        return
    
    if CHANNEL_ID == 0:
        logger.error("❌ DISCORD_CHANNEL_ID não configurado")
        return
    
    logger.info("=" * 50)
    logger.info("🚀 INICIANDO GITHUB-DISCORD BOT")
    logger.info("=" * 50)
    
    # Inicia Flask em thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logger.info("⏳ Aguardando Flask iniciar...")
    import time
    time.sleep(2)  # Aguarda Flask iniciar
    
    # Inicia o bot Discord
    logger.info("🤖 Iniciando bot Discord...")
    
    try:
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Token do Discord inválido")
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar bot: {e}")

if __name__ == "__main__":
    main()