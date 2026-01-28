import discord

import discord
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import json
import aiohttp
from datetime import datetime
import asyncio

# Para webhook do GitHub
from flask import Flask, request, jsonify
import threading
import requests

# Carrega variáveis de ambiente
load_dotenv()

# Configurações
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GITHUB_SECRET = os.getenv('GITHUB_WEBHOOK_SECRET', '')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))
REPO_OWNER = os.getenv('GITHUB_REPO_OWNER', 'seu_usuario')
REPO_NAME = os.getenv('GITHUB_REPO_NAME', 'seu_repositorio')

# Cria o bot do Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Cria servidor Flask para webhooks
flask_app = Flask(__name__)

@flask_app.route('/github-webhook', methods=['POST'])
def github_webhook():
    """Endpoint para receber webhooks do GitHub"""
    
    # Verifica o cabeçalho do evento
    event_type = request.headers.get('X-GitHub-Event')
    
    if event_type == 'push':
        data = request.json
        
        # Verifica se é na branch main
        if data.get('ref') == 'refs/heads/main':
            # Processa o push de forma assíncrona
            asyncio.run_coroutine_threadsafe(
                process_github_push(data), 
                bot.loop
            )
    
    return jsonify({'status': 'received'}), 200

async def process_github_push(data):
    """Processa os dados do push e envia para o Discord"""
    
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"Canal com ID {CHANNEL_ID} não encontrado!")
        return
    
    repo = data['repository']
    pusher = data['pusher']
    commits = data['commits']
    ref = data['ref']
    branch = ref.split('/')[-1]
    
    # Cria um embed para o Discord
    embed = discord.Embed(
        title=f"📦 Push detectado em {repo['full_name']}",
        description=f"**Branch:** `{branch}`\n**Commits:** {len(commits)}",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    
    # Adiciona informações do repositório
    embed.set_author(
        name=pusher['name'],
        icon_url=pusher.get('avatar_url', '')
    )
    
    embed.add_field(
        name="Repositório",
        value=f"[{repo['full_name']}]({repo['html_url']})",
        inline=True
    )
    
    embed.add_field(
        name="Compare",
        value=f"[Ver alterações]({data['compare']})",
        inline=True
    )
    
    for i, commit in enumerate(commits[:5]):
        short_sha = commit['id'][:7]
        commit_message = commit['message'].split('\n')[0][:50] + "..." if len(commit['message']) > 50 else commit['message']
        
        embed.add_field(
            name=f"Commit {i+1}: `{short_sha}`",
            value=f"{commit_message}\n[Ver commit]({commit['url']})",
            inline=False
        )
    
    if len(commits) > 5:
        embed.add_field(
            name="Mais commits",
            value=f"{len(commits) - 5} commits adicionais...",
            inline=False
        )
    
    embed.set_footer(text=f"Push realizado por {pusher['name']}")
    
    try:
        await channel.send(embed=embed)
        print(f"Mensagem enviada para o canal {CHANNEL_ID}")
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")

@bot.event
async def on_ready():
    print(f'Bot conectado como {bot.user}')
    
    # Inicia o servidor Flask em segundo plano
    def run_flask():
        flask_app.run(host='0.0.0.0', port=5000)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Servidor webhook iniciado na porta 5000")

# Comando para testar o bot
@bot.command(name='teste')
async def teste(ctx):
    """Comando para testar se o bot está funcionando"""
    await ctx.send(f'Bot funcionando! Canal ID: {CHANNEL_ID}')

# Comando para configurar rapidamente
@bot.command(name='setup')
async def setup(ctx):
    """Instruções para configurar o webhook do GitHub"""
    message = """
    **Configuração do Webhook GitHub:**
    
    1. Vá para seu repositório no GitHub
    2. Settings → Webhooks → Add webhook
    3. Payload URL: `http://seu-servidor:5000/github-webhook`
    4. Content type: `application/json`
    5. Eventos: Selecionar "Just the push event"
    6. Clicar em "Add webhook"
    
    *Substitua `seu-servidor` pelo IP/domínio do seu servidor*
    """
    await ctx.send(message)

if __name__ == "__main__":
    # Inicia o bot do Discord
    bot.run(DISCORD_TOKEN)