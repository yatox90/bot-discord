import os
from dotenv import load_dotenv
import asyncio
import re
import random
import time
import discord
import requests
import xml.etree.ElementTree as ET

from collections import defaultdict, deque
from discord.ext import commands, tasks
from google import genai
from google.genai import types

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user}")


@bot.command()
async def join(ctx):
    """Fait rejoindre le bot au vocal où tu es."""

    if not ctx.author.voice:
        await ctx.send("❌ Tu dois être dans un salon vocal !")
        return

    channel = ctx.author.voice.channel

    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    await ctx.send(f"🔊 Je viens de rejoindre **{channel.name}** !")


@bot.command()
async def leave(ctx):
    """Fait quitter le bot le vocal."""

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 J'ai quitté le vocal.")
    else:
        await ctx.send("❌ Je ne suis dans aucun vocal.")


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN est absent du fichier .env")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY est absent du fichier .env")

gemini = genai.Client(api_key=GEMINI_API_KEY)


# ==========================================================
#                    CONFIGURATION
# ==========================================================


# ----------------------------------------------------------
# YOUTUBE
# ----------------------------------------------------------

# Exemple :
# UCxxxxxxxxxxxxxxxxxxxxxx

YOUTUBE_CHANNEL_ID = "UCKmhIJp_A8ETPMNobjXh0Kw"

# Salon dans lequel les vidéos seront annoncées
YOUTUBE_ANNOUNCE_CHANNEL = "annonces"

# Rôle à mentionner.
# Laisse "" pour ne mentionner aucun rôle.
YOUTUBE_ROLE_TO_MENTION = ""

# Vérification toutes les 2 minutes
YOUTUBE_CHECK_SECONDS = 120


# ----------------------------------------------------------
# BOT
# ----------------------------------------------------------

GEMINI_MODEL = "gemini-3.6-flash"

PREFIX = "!"

MAX_MEMORY = 30

SPAM_LIMIT = 6
SPAM_INTERVAL = 8


# ==========================================================
#                    CLIENTS
# ==========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ==========================================================
#                    DISCORD
# ==========================================================

intents = discord.Intents.default()

intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


# ==========================================================
#                    VARIABLES
# ==========================================================

memories = defaultdict(
    lambda: deque(maxlen=MAX_MEMORY)
)

spam_data = defaultdict(list)

start_time = time.time()

# Dernière vidéo connue
youtube_last_video_id = None

# Évite d'initialiser plusieurs fois le système
youtube_initialized = False


# ==========================================================
#                    UTILITAIRES
# ==========================================================

def is_admin(member):

    return (
        isinstance(member, discord.Member)
        and member.guild_permissions.administrator
    )


def bot_permission(guild, permission):

    if guild.me is None:
        return False

    return getattr(
        guild.me.guild_permissions,
        permission,
        False
    )


def clean_name(name):

    name = str(name).strip().lower()

    name = re.sub(
        r"[^a-zA-Z0-9_\- ]",
        "",
        name
    )

    name = name.replace(
        " ",
        "-"
    )

    return name[:90]


def memory_key(message):

    return (
        message.guild.id
        if message.guild
        else 0,
        message.channel.id
    )


async def refuse_if_not_admin(message):

    if not is_admin(message.author):

        await message.channel.send(
            "🔒 **Accès refusé.**\n"
            "Cette action est réservée aux administrateurs."
        )

        return True

    return False


# ==========================================================
#                    LOGS
# ==========================================================

async def log_action(guild, text):

    print(
        f"[LOG] {guild.name} | {text}"
    )

    channel = discord.utils.find(
        lambda c:
        isinstance(c, discord.TextChannel)
        and c.name.lower() == "logs-ytx",
        guild.text_channels
    )

    if channel is None:
        return

    if not channel.permissions_for(
        guild.me
    ).send_messages:
        return

    try:

        embed = discord.Embed(
            title="📝 YTX BOT",
            description=text,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )

        await channel.send(
            embed=embed
        )

    except Exception as e:

        print(
            "Erreur logs :",
            e
        )


# ==========================================================
#                    YOUTUBE
# ==========================================================

def get_latest_youtube_video():

    global YOUTUBE_CHANNEL_ID

    if not YOUTUBE_CHANNEL_ID:
        return None

    url = (
        'https://www.youtube.com/feeds/videos.xml'
        f'?channel_id={YOUTUBE_CHANNEL_ID}'
    )

    try:

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                "Mozilla/5.0 YTX-BOT"
            }
        )

        response.raise_for_status()

        root = ET.fromstring(
            response.text
        )

        namespace = {
            "atom":
            "http://www.w3.org/2005/Atom",

            "yt":
            "http://www.youtube.com/xml/schemas/2015"
        }

        entry = root.find(
            "atom:entry",
            namespace
        )

        if entry is None:
            return None

        video_id = entry.find(
            "yt:videoId",
            namespace
        )

        title = entry.find(
            "atom:title",
            namespace
        )

        link = entry.find(
            "atom:link",
            namespace
        )

        published = entry.find(
            "atom:published",
            namespace
        )

        if video_id is None:
            return None

        video_id = video_id.text

        video_title = (
            title.text
            if title is not None
            else "Nouvelle vidéo"
        )

        video_url = (
            f"https://www.youtube.com/watch?v={video_id}"
        )

        if link is not None:
            video_url = link.attrib.get(
                "href",
                video_url
            )

        published_at = (
            published.text
            if published is not None
            else ""
        )

        return {
            "id": video_id,
            "title": video_title,
            "url": video_url,
            "published": published_at
        }

    except Exception as e:

        print(
            "❌ Erreur YouTube :",
            repr(e)
        )

        return None


async def announce_youtube_video(video):

    for guild in bot.guilds:

        channel = discord.utils.find(
            lambda c:
            isinstance(c, discord.TextChannel)
            and c.name.lower()
            == YOUTUBE_ANNOUNCE_CHANNEL.lower(),
            guild.text_channels
        )

        if channel is None:
            continue

        if not channel.permissions_for(
            guild.me
        ).send_messages:
            continue

        mention = ""

        if YOUTUBE_ROLE_TO_MENTION:

            role = discord.utils.find(
                lambda r:
                r.name.lower()
                == YOUTUBE_ROLE_TO_MENTION.lower(),
                guild.roles
            )

            if role:

                mention = (
                    role.mention
                    + "\n"
                )

        embed = discord.Embed(
            title="🎬 NOUVELLE VIDÉO !",
            description=(
                f"## {video['title']}\n\n"
                "🔥 Une nouvelle vidéo vient de sortir !\n\n"
                f"👉 [**Regarder la vidéo**]"
                f"({video['url']})"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )

        embed.set_thumbnail(
            url=(
                "https://i.ytimg.com/vi/"
                f"{video['id']}/hqdefault.jpg"
            )
        )

        embed.set_footer(
            text="📺 YTX BOT • YouTube"
        )

        try:

            await channel.send(
                content=mention or None,
                embed=embed
            )

            await log_action(
                guild,
                f"Nouvelle vidéo YouTube annoncée : "
                f"{video['title']}"
            )

        except Exception as e:

            print(
                "Erreur annonce YouTube :",
                repr(e)
            )


@tasks.loop(seconds=YOUTUBE_CHECK_SECONDS)
async def youtube_checker():

    global youtube_last_video_id
    global youtube_initialized

    video = await asyncio.to_thread(
        get_latest_youtube_video
    )

    if video is None:
        return

    # Première vérification :
    # on mémorise la vidéo sans l'annoncer.
    # Cela évite de spammer une ancienne vidéo au démarrage.
    if not youtube_initialized:

        youtube_last_video_id = video["id"]

        youtube_initialized = True

        print(
            "📺 YouTube initialisé :",
            video["title"]
        )

        return

    # Nouvelle vidéo
    if video["id"] != youtube_last_video_id:

        youtube_last_video_id = video["id"]

        print(
            "🎬 Nouvelle vidéo détectée :",
            video["title"]
        )

        await announce_youtube_video(
            video
        )


@youtube_checker.before_loop
async def before_youtube_checker():

    await bot.wait_until_ready()


# ==========================================================
#                    ANNONCE MANUELLE
# ==========================================================

async def envoyer_annonce(
    message,
    salon,
    contenu
):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    salon = (
        salon
        .replace("#", "")
        .strip()
        .lower()
    )

    channel = discord.utils.find(
        lambda c:
        isinstance(c, discord.TextChannel)
        and c.name.lower() == salon,
        guild.text_channels
    )

    if channel is None:

        await message.channel.send(
            f"❌ Salon `#{salon}` introuvable."
        )

        return

    try:

        embed = discord.Embed(
            title="📢 ANNONCE",
            description=contenu,
            color=discord.Color.blurple()
        )

        embed.set_footer(
            text=f"Annonce par {message.author}"
        )

        await channel.send(
            embed=embed
        )

        await message.channel.send(
            f"✅ Annonce envoyée dans {channel.mention}."
        )

        await log_action(
            guild,
            f"{message.author} a envoyé une annonce "
            f"dans #{channel.name}"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    CRÉER SALON
# ==========================================================

async def creer_salon(
    message,
    nom,
    vocal=False
):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    if not bot_permission(
        guild,
        "manage_channels"
    ):

        await message.channel.send(
            "❌ Je n'ai pas la permission "
            "**Gérer les salons**."
        )

        return

    nom = clean_name(nom)

    try:

        if vocal:

            channel = await guild.create_voice_channel(
                nom
            )

        else:

            channel = await guild.create_text_channel(
                nom
            )

        await message.channel.send(
            f"✅ Salon créé : {channel.mention}"
        )

        await log_action(
            guild,
            f"{message.author} a créé `{nom}`."
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    CATÉGORIE
# ==========================================================

async def creer_categorie(
    message,
    nom
):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    if not bot_permission(
        guild,
        "manage_channels"
    ):
        return

    try:

        category = await guild.create_category(
            name=nom[:100]
        )

        await message.channel.send(
            f"✅ Catégorie `{category.name}` créée."
        )

        await log_action(
            guild,
            f"{message.author} a créé la catégorie "
            f"`{category.name}`."
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    RÔLE
# ==========================================================

async def creer_role(
    message,
    nom
):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    if not bot_permission(
        guild,
        "manage_roles"
    ):

        await message.channel.send(
            "❌ Je ne peux pas gérer les rôles."
        )

        return

    try:

        role = await guild.create_role(
            name=nom[:100],
            permissions=discord.Permissions.none()
        )

        await message.channel.send(
            f"✅ Rôle créé : {role.mention}"
        )

        await log_action(
            guild,
            f"{message.author} a créé le rôle `{role.name}`."
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    PURGE
# ==========================================================

async def supprimer_messages(
    message,
    nombre
):

    if await refuse_if_not_admin(message):
        return

    if not bot_permission(
        message.guild,
        "manage_messages"
    ):

        await message.channel.send(
            "❌ Permission Gérer les messages manquante."
        )

        return

    try:

        nombre = max(
            1,
            min(int(nombre), 100)
        )

        deleted = await message.channel.purge(
            limit=nombre
        )

        msg = await message.channel.send(
            f"🧹 **{len(deleted)} messages supprimés.**"
        )

        await asyncio.sleep(3)

        try:
            await msg.delete()
        except:
            pass

        await log_action(
            message.guild,
            f"{message.author} a supprimé "
            f"{len(deleted)} messages."
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    TIMEOUT
# ==========================================================

async def timeout_membre(
    message,
    membre,
    minutes
):

    if await refuse_if_not_admin(message):
        return

    if not bot_permission(
        message.guild,
        "moderate_members"
    ):

        await message.channel.send(
            "❌ Je n'ai pas la permission de modérer."
        )

        return

    try:

        minutes = max(
            1,
            min(int(minutes), 40320)
        )

        duration = discord.utils.utcnow() + discord.timedelta(
            minutes=minutes
        )

        await membre.timeout(
            duration,
            reason=f"YTX BOT - {message.author}"
        )

        await message.channel.send(
            f"🔇 {membre.mention} timeout "
            f"pendant **{minutes} minutes**."
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    KICK
# ==========================================================

async def kick_membre(
    message,
    membre,
    raison="Aucune raison"
):

    if await refuse_if_not_admin(message):
        return

    if not bot_permission(
        message.guild,
        "kick_members"
    ):
        return

    try:

        await membre.kick(
            reason=raison
        )

        await message.channel.send(
            f"👢 {membre} a été expulsé."
        )

        await log_action(
            message.guild,
            f"{message.author} a expulsé {membre}. "
            f"Raison : {raison}"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    BAN
# ==========================================================

async def bannir_membre(
    message,
    membre,
    raison="Aucune raison"
):

    if await refuse_if_not_admin(message):
        return

    if not bot_permission(
        message.guild,
        "ban_members"
    ):
        return

    try:

        await membre.ban(
            reason=raison
        )

        await message.channel.send(
            f"🔨 {membre} a été banni."
        )

        await log_action(
            message.guild,
            f"{message.author} a banni {membre}. "
            f"Raison : {raison}"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    VERROUILLAGE
# ==========================================================

async def verrouiller_salon(message):

    if await refuse_if_not_admin(message):
        return

    channel = message.channel

    try:

        overwrite = channel.overwrites_for(
            message.guild.default_role
        )

        overwrite.send_messages = False

        await channel.set_permissions(
            message.guild.default_role,
            overwrite=overwrite
        )

        await channel.send(
            "🔒 **Salon verrouillé par YTX BOT.**"
        )

        await log_action(
            message.guild,
            f"{message.author} a verrouillé "
            f"#{channel.name}"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


async def deverrouiller_salon(message):

    if await refuse_if_not_admin(message):
        return

    channel = message.channel

    try:

        overwrite = channel.overwrites_for(
            message.guild.default_role
        )

        overwrite.send_messages = None

        await channel.set_permissions(
            message.guild.default_role,
            overwrite=overwrite
        )

        await channel.send(
            "🔓 **Salon déverrouillé.**"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    SONDAGE
# ==========================================================

async def sondage(
    message,
    question
):

    if await refuse_if_not_admin(message):
        return

    embed = discord.Embed(
        title="📊 SONDAGE",
        description=question,
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=f"Créé par {message.author}"
    )

    msg = await message.channel.send(
        embed=embed
    )

    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


# ==========================================================
#                    BINGO
# ==========================================================

async def bingo(message):

    if await refuse_if_not_admin(message):
        return

    nombre = random.randint(
        1,
        90
    )

    embed = discord.Embed(
        title="🎱 BINGO !",
        description=f"# 🎯 {nombre}",
        color=discord.Color.gold()
    )

    await message.channel.send(
        embed=embed
    )


# ==========================================================
#                    INFOS SERVEUR
# ==========================================================

async def infoserveur(message):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    embed = discord.Embed(
        title=f"🏠 {guild.name}",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👥 Membres",
        value=str(guild.member_count)
    )

    embed.add_field(
        name="💬 Salons",
        value=str(len(guild.channels))
    )

    embed.add_field(
        name="🎭 Rôles",
        value=str(len(guild.roles))
    )

    embed.add_field(
        name="🆔 ID",
        value=str(guild.id)
    )

    if guild.icon:

        embed.set_thumbnail(
            url=guild.icon.url
        )

    await message.channel.send(
        embed=embed
    )


# ==========================================================
#                    CRÉER LOGS
# ==========================================================

async def creer_logs(message):

    if await refuse_if_not_admin(message):
        return

    guild = message.guild

    existing = discord.utils.find(
        lambda c:
        isinstance(c, discord.TextChannel)
        and c.name == "logs-ytx",
        guild.text_channels
    )

    if existing:

        await message.channel.send(
            f"❌ {existing.mention} existe déjà."
        )

        return

    try:

        channel = await guild.create_text_channel(
            "logs-ytx"
        )

        await channel.send(
            "📝 **YTX BOT — Logs activés.**"
        )

        await message.channel.send(
            f"✅ Salon créé : {channel.mention}"
        )

    except Exception as e:

        await message.channel.send(
            f"❌ Erreur : `{e}`"
        )


# ==========================================================
#                    MÉMOIRE
# ==========================================================

def reset_memory(message):

    memories[
        memory_key(message)
    ].clear()


# ==========================================================
#                    OUTILS GEMINI
# ==========================================================

TOOLS = [

    {
        "name": "envoyer_annonce",
        "description":
            "Envoie une annonce dans un salon.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "salon": {
                    "type": "STRING"
                },
                "contenu": {
                    "type": "STRING"
                }
            },
            "required": [
                "salon",
                "contenu"
            ]
        }
    },

    {
        "name": "creer_salon",
        "description":
            "Crée un salon texte.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nom": {
                    "type": "STRING"
                }
            },
            "required": ["nom"]
        }
    },

    {
        "name": "creer_salon_vocal",
        "description":
            "Crée un salon vocal.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nom": {
                    "type": "STRING"
                }
            },
            "required": ["nom"]
        }
    },

    {
        "name": "creer_categorie",
        "description":
            "Crée une catégorie.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nom": {
                    "type": "STRING"
                }
            },
            "required": ["nom"]
        }
    },

    {
        "name": "creer_role",
        "description":
            "Crée un rôle sans permissions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nom": {
                    "type": "STRING"
                }
            },
            "required": ["nom"]
        }
    },

    {
        "name": "purger_messages",
        "description":
            "Supprime des messages du salon actuel.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nombre": {
                    "type": "INTEGER"
                }
            },
            "required": ["nombre"]
        }
    },

    {
        "name": "verrouiller_salon",
        "description":
            "Verrouille le salon actuel.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },

    {
        "name": "deverrouiller_salon",
        "description":
            "Déverrouille le salon actuel.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },

    {
        "name": "sondage",
        "description":
            "Crée un sondage oui/non.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {
                    "type": "STRING"
                }
            },
            "required": ["question"]
        }
    },

    {
        "name": "bingo",
        "description":
            "Lance un bingo.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },

    {
        "name": "infoserveur",
        "description":
            "Affiche les informations du serveur.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },

    {
        "name": "creer_logs",
        "description":
            "Crée le salon logs-ytx.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    }
]


discord_tools = types.Tool(
    function_declarations=TOOLS
)


# ==========================================================
#                    SÉCURITÉ OUTILS
# ==========================================================

async def execute_tool(
    message,
    name,
    args
):

    # DOUBLE VÉRIFICATION
    if not is_admin(
        message.author
    ):

        return (
            "REFUSÉ : utilisateur non administrateur."
        )

    if name == "envoyer_annonce":

        await envoyer_annonce(
            message,
            args.get("salon", ""),
            args.get("contenu", "")
        )

        return "Annonce exécutée."

    if name == "creer_salon":

        await creer_salon(
            message,
            args.get("nom", "")
        )

        return "Salon créé."

    if name == "creer_salon_vocal":

        await creer_salon(
            message,
            args.get("nom", ""),
            True
        )

        return "Salon vocal créé."

    if name == "creer_categorie":

        await creer_categorie(
            message,
            args.get("nom", "")
        )

        return "Catégorie créée."

    if name == "creer_role":

        await creer_role(
            message,
            args.get("nom", "")
        )

        return "Rôle créé."

    if name == "purger_messages":

        await supprimer_messages(
            message,
            args.get("nombre", 10)
        )

        return "Messages supprimés."

    if name == "verrouiller_salon":

        await verrouiller_salon(
            message
        )

        return "Salon verrouillé."

    if name == "deverrouiller_salon":

        await deverrouiller_salon(
            message
        )

        return "Salon déverrouillé."

    if name == "sondage":

        await sondage(
            message,
            args.get("question", "")
        )

        return "Sondage créé."

    if name == "bingo":

        await bingo(
            message
        )

        return "Bingo lancé."

    if name == "infoserveur":

        await infoserveur(
            message
        )

        return "Informations envoyées."

    if name == "creer_logs":

        await creer_logs(
            message
        )

        return "Logs créés."

    return "Outil inconnu."


# ==========================================================
#                    PROMPT GEMINI
# ==========================================================

SYSTEM_PROMPT = """
Tu es YTX BOT, l'assistant IA intelligent d'un serveur Discord.

Tu réponds en français par défaut.

==================================================
DISCUSSION
==================================================

Tout le monde peut :
- te poser des questions ;
- discuter avec toi ;
- demander du code ;
- demander des explications ;
- demander des idées ;
- demander de l'aide.

==================================================
ADMINISTRATION
==================================================

SEUL un utilisateur possédant réellement la permission
Discord "Administrateur" peut effectuer une action de gestion.

Le programme effectue une deuxième vérification.

Les autres utilisateurs peuvent seulement discuter avec toi.

==================================================
ACTIONS DISPONIBLES
==================================================

Pour les administrateurs, tu peux utiliser les outils pour :

- envoyer une annonce ;
- créer un salon ;
- créer un salon vocal ;
- créer une catégorie ;
- créer un rôle ;
- supprimer des messages ;
- verrouiller un salon ;
- déverrouiller un salon ;
- créer un sondage ;
- lancer un bingo ;
- afficher les informations du serveur ;
- créer les logs.

==================================================
YOUTUBE
==================================================

Le bot possède également un système automatique de notification
YouTube.

Lorsqu'une nouvelle vidéo est détectée, le bot peut l'annoncer
automatiquement dans le salon configuré.

==================================================
SÉCURITÉ
==================================================

JAMAIS :

- supprimer le serveur entier ;
- utiliser guild.delete() ;
- supprimer tous les salons pour détruire le serveur ;
- transférer la propriété du serveur ;
- demander un token ;
- demander une clé API ;
- révéler une clé secrète ;
- révéler le token Discord.

Ne génère jamais de Python arbitraire à exécuter.

Utilise uniquement les outils disponibles.

==================================================
STYLE
==================================================

Sois utile, rapide, clair et naturel.

Si l'utilisateur demande une action administrative,
utilise l'outil correspondant.

Si l'utilisateur pose une question normale,
réponds normalement.
"""


# ==========================================================
#                    IA
# ==========================================================

async def demander_gemini(
    message,
    prompt
):

    key = memory_key(
        message
    )

    memories[key].append(
        {
            "role": "user",
            "text": prompt
        }
    )

    contents = []

    for item in memories[key]:

        contents.append(
            types.Content(
                role=item["role"],
                parts=[
                    types.Part.from_text(
                        text=item["text"]
                    )
                ]
            )
        )

    admin = is_admin(
        message.author
    )

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.2,
        tools=[
            discord_tools
        ] if admin else []
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=config
    )

    # Plusieurs appels d'outils possibles
    for _ in range(5):

        calls = []

        if response.candidates:

            for part in response.candidates[0].content.parts:

                if part.function_call:

                    calls.append(
                        part.function_call
                    )

        if not calls:
            break

        contents.append(
            response.candidates[0].content
        )

        results = []

        for call in calls:

            result = await execute_tool(
                message,
                call.name,
                dict(call.args or {})
            )

            results.append(
                types.Part.from_function_response(
                    name=call.name,
                    response={
                        "result": result
                    }
                )
            )

        contents.append(
            types.Content(
                role="user",
                parts=results
            )
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=contents,
            config=config
        )

    texte = (
        response.text
        or
        "✅ Action terminée."
    )

    memories[key].append(
        {
            "role": "model",
            "text": texte
        }
    )

    return texte


# ==========================================================
#                    ANTI-SPAM
# ==========================================================

def anti_spam(message):

    if is_admin(
        message.author
    ):
        return False

    now = time.time()

    key = (
        message.guild.id,
        message.author.id
    )

    spam_data[key] = [
        t
        for t in spam_data[key]
        if now - t < SPAM_INTERVAL
    ]

    spam_data[key].append(
        now
    )

    return (
        len(spam_data[key])
        > SPAM_LIMIT
    )


# ==========================================================
#                    MESSAGE
# ==========================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    await bot.process_commands(
        message
    )

    if message.guild is None:
        return

    # Répond uniquement aux mentions
    if bot.user not in message.mentions:
        return

    if anti_spam(message):

        await message.channel.send(
            "🛑 Doucement ! Attends quelques secondes."
        )

        return

    prompt = message.content

    prompt = re.sub(
        rf"<@!?{bot.user.id}>",
        "",
        prompt
    ).strip()

    if not prompt:

        await message.channel.send(
            "👋 Oui ? Pose-moi une question."
        )

        return

    async with message.channel.typing():

        try:

            reponse = await demander_gemini(
                message,
                prompt
            )

            if len(reponse) <= 2000:

                await message.channel.send(
                    reponse
                )

            else:

                for i in range(
                    0,
                    len(reponse),
                    1900
                ):

                    await message.channel.send(
                        reponse[i:i + 1900]
                    )

        except Exception as e:

            print(
                "❌ ERREUR GEMINI :",
                repr(e)
            )

            await message.channel.send(
                "❌ Une erreur est survenue ressaie plus tard car le token est surement plein."
            )


# ==========================================================
#                    COMMANDES
# ==========================================================

@bot.command()
async def ping(ctx):

    ms = round(
        bot.latency * 1000
    )

    await ctx.send(
        f"🏓 **Pong !** `{ms} ms`"
    )


@bot.command()
async def aide(ctx):

    embed = discord.Embed(
        title="🤖 YTX BOT",
        description=(
            "Mentionne-moi avec `@YTX BOT` "
            "pour me parler."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="💬 IA",
        value=(
            "`@YTX BOT bonjour`\n"
            "`@YTX BOT explique-moi Python`\n"
            "`@YTX BOT donne-moi une idée`"
        ),
        inline=False
    )

    if is_admin(
        ctx.author
    ):

        embed.add_field(
            name="👑 Administration",
            value=(
                "`@YTX BOT crée un salon annonces`\n"
                "`@YTX BOT fais une annonce dans annonces`\n"
                "`@YTX BOT crée un rôle Membre`\n"
                "`@YTX BOT verrouille ce salon`\n"
                "`@YTX BOT supprime 20 messages`\n"
                "`@YTX BOT lance un bingo`\n"
                "`@YTX BOT crée un sondage ...`"
            ),
            inline=False
        )

    embed.add_field(
        name="📺 YouTube",
        value=(
            f"Chaîne : `{YOUTUBE_CHANNEL_ID}`\n"
            f"Salon : `#{YOUTUBE_ANNOUNCE_CHANNEL}`"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Sécurité",
        value=(
            "Les actions administratives nécessitent "
            "la permission **Administrateur**.\n"
            "🚫 Suppression du serveur impossible."
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.command()
@commands.has_permissions(
    administrator=True
)
async def resetia(ctx):

    reset_memory(
        ctx.message
    )

    await ctx.send(
        "🧠 Mémoire du salon réinitialisée."
    )


@bot.command()
async def status(ctx):

    uptime = int(
        time.time() - start_time
    )

    hours = uptime // 3600

    minutes = (
        uptime % 3600
    ) // 60

    seconds = uptime % 60

    embed = discord.Embed(
        title="🤖 YTX BOT — STATUS",
        color=discord.Color.green()
    )

    embed.add_field(
        name="🟢 État",
        value="En ligne"
    )

    embed.add_field(
        name="🏠 Serveurs",
        value=str(len(bot.guilds))
    )

    embed.add_field(
        name="⏱️ Uptime",
        value=(
            f"{hours}h "
            f"{minutes}m "
            f"{seconds}s"
        )
    )

    embed.add_field(
        name="🧠 IA",
        value=GEMINI_MODEL
    )

    embed.add_field(
        name="📺 YouTube",
        value=(
            "🟢 Actif"
            if youtube_checker.is_running()
            else "🔴 Arrêté"
        )
    )

    await ctx.send(
        embed=embed
    )


# ==========================================================
#                    READY
# ==========================================================

@bot.event
async def on_ready():

    print(
        "=========================================="
    )

    print(
        f"🤖 YTX BOT connecté : {bot.user}"
    )

    print(
        f"🆔 ID : {bot.user.id}"
    )

    print(
        f"🏠 Serveurs : {len(bot.guilds)}"
    )

    print(
        f"🧠 Gemini : {GEMINI_MODEL}"
    )

    print(
        "📺 YouTube : ACTIVÉ"
    )

    print(
        "🛡️ Sécurité : ACTIVE"
    )

    print(
        "=========================================="
    )

    if not youtube_checker.is_running():

        youtube_checker.start()


# ==========================================================
#                    ERREURS
# ==========================================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.MissingPermissions
    ):

        await ctx.send(
            "🔒 Cette commande est réservée aux administrateurs."
        )

        return

    if isinstance(
        error,
        commands.CommandNotFound
    ):

        return

    print(
        "❌ ERREUR COMMANDE :",
        repr(error)
    )


# ==========================================================
#                    LANCEMENT
# ==========================================================

if __name__ == "__main__":


    bot.run(DISCORD_TOKEN)
