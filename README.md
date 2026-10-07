# -*- coding: utf-8 -*-
"""
=========================================================
                 YTX OSINT V2
      Outil OSINT public + IA locale Ollama
=========================================================

Installation :
    pip install flask requests

Puis :
    python ytx_osint.py

Ollama :
    ollama pull llama3.2

Interface :
    http://127.0.0.1:5000
"""

from flask import Flask, request, jsonify, Response
import requests
import json
import re
import time
from datetime import datetime

app = Flask(__name__)

# =========================================================
# CONFIGURATION
# =========================================================

HOST = "127.0.0.1"
PORT = 5000

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2"

MAX_HISTORY = 50
REQUEST_TIMEOUT = 15
AI_TIMEOUT = 120

history = []

# =========================================================
# UTILITAIRES
# =========================================================

def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def clean_text(value, max_length=5000):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()

    return value[:max_length]


def safe_url(url):
    if not url:
        return ""

    url = str(url).strip()

    if url.startswith("https://") or url.startswith("http://"):
        return url

    return ""


# =========================================================
# DETECTION
# =========================================================

def detect_entities(query):
    query = query.strip()

    entities = []

    usernames = re.findall(
        r"(?<!\w)@([A-Za-z0-9_.-]{2,50})",
        query
    )

    for username in usernames:
        entities.append({
            "type": "username",
            "value": username
        })

    domains = re.findall(
        r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|fr|net|org|io|dev|ai|co|uk|de|be|ch)\b",
        query.lower()
    )

    for domain in domains:
        entities.append({
            "type": "domain",
            "value": domain
        })

    urls = re.findall(
        r"https?://[^\s]+",
        query
    )

    for url in urls:
        entities.append({
            "type": "url",
            "value": url
        })

    if len(query.split()) >= 2:
        entities.append({
            "type": "query",
            "value": query
        })

    return entities


# =========================================================
# SCORE DE CONFIANCE
# =========================================================

def calculate_confidence(results):
    if not results:
        return {
            "label": "FAIBLE",
            "score": 10,
            "reason": "Aucune source exploitable."
        }

    valid = 0
    sources = set()

    for result in results:
        if result.get("title") or result.get("url"):
            valid += 1

        url = result.get("url", "")

        if url:
            match = re.search(
                r"https?://([^/]+)",
                url
            )

            if match:
                sources.add(match.group(1).lower())

    score = 20

    score += min(valid * 10, 40)
    score += min(len(sources) * 8, 40)

    score = min(score, 100)

    if score >= 75:
        label = "ÉLEVÉE"
    elif score >= 45:
        label = "MOYENNE"
    else:
        label = "FAIBLE"

    return {
        "label": label,
        "score": score,
        "reason": f"{valid} résultat(s), {len(sources)} source(s) distincte(s)."
    }


# =========================================================
# DUCKDUCKGO
# =========================================================

def duckduckgo_search(query):
    """
    Recherche via l'API publique DuckDuckGo Instant Answer.

    Cette API ne remplace pas un moteur de recherche complet.
    Elle permet principalement de récupérer des informations
    publiques et des réponses instantanées.
    """

    try:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        results = []

        abstract = data.get("AbstractText", "")
        abstract_url = data.get("AbstractURL", "")
        heading = data.get("Heading", "")

        if abstract:
            results.append({
                "title": heading or "DuckDuckGo — Résumé",
                "description": clean_text(abstract, 1500),
                "url": safe_url(abstract_url),
                "source": "DuckDuckGo",
                "type": "public_information"
            })

        related = data.get("RelatedTopics", [])

        def parse_topics(items):
            found = []

            for item in items:
                if "Topics" in item:
                    found.extend(
                        parse_topics(item.get("Topics", []))
                    )

                elif item.get("Text"):
                    found.append(item)

            return found

        topics = parse_topics(related)

        for item in topics[:10]:

            url = safe_url(item.get("FirstURL", ""))

            if not url:
                continue

            results.append({
                "title": clean_text(
                    item.get("Text", "Résultat"),
                    300
                ),
                "description": clean_text(
                    item.get("Text", ""),
                    700
                ),
                "url": url,
                "source": "DuckDuckGo",
                "type": "related_result"
            })

        return results

    except Exception as e:

        return [{
            "title": "Recherche indisponible",
            "description": str(e),
            "url": "",
            "source": "DuckDuckGo",
            "type": "error"
        }]


# =========================================================
# LIENS DE RECHERCHE PUBLIQUE
# =========================================================

def build_public_searches(query):

    encoded = requests.utils.quote(query)

    return [

        {
            "title": "Recherche Web",
            "description": "Ouvrir la recherche publique DuckDuckGo.",
            "url": f"https://duckduckgo.com/?q={encoded}",
            "source": "DuckDuckGo",
            "type": "search_engine"
        },

        {
            "title": "Google",
            "description": "Recherche publique Google.",
            "url": f"https://www.google.com/search?q={encoded}",
            "source": "Google",
            "type": "search_engine"
        },

        {
            "title": "Bing",
            "description": "Recherche publique Bing.",
            "url": f"https://www.bing.com/search?q={encoded}",
            "source": "Bing",
            "type": "search_engine"
        },

        {
            "title": "GitHub",
            "description": "Recherche publique GitHub.",
            "url": f"https://github.com/search?q={encoded}&type=users",
            "source": "GitHub",
            "type": "public_search"
        },

        {
            "title": "Reddit",
            "description": "Recherche publique Reddit.",
            "url": f"https://www.reddit.com/search/?q={encoded}",
            "source": "Reddit",
            "type": "public_search"
        }
    ]


# =========================================================
# RECHERCHE PRINCIPALE
# =========================================================

def perform_search(query):

    query = clean_text(query, 500)

    if not query:
        return {
            "query": "",
            "results": [],
            "entities": [],
            "confidence": calculate_confidence([]),
            "timestamp": now()
        }

    results = duckduckgo_search(query)

    public_links = build_public_searches(query)

    results.extend(public_links)

    # Supprimer les doublons d'URL
    unique = []
    seen = set()

    for result in results:

        url = result.get("url", "")

        if url and url in seen:
            continue

        if url:
            seen.add(url)

        unique.append(result)

    results = unique[:30]

    confidence = calculate_confidence(results)

    data = {
        "query": query,
        "results": results,
        "entities": detect_entities(query),
        "confidence": confidence,
        "timestamp": now()
    }

    # Historique
    history.insert(0, data)

    if len(history) > MAX_HISTORY:
        del history[MAX_HISTORY:]

    return data


# =========================================================
# OLLAMA
# =========================================================

def ollama_status():

    try:

        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=5
        )

        if response.status_code != 200:
            return {
                "online": False,
                "models": []
            }

        data = response.json()

        models = []

        for model in data.get("models", []):
            name = model.get("name")

            if name:
                models.append(name)

        return {
            "online": True,
            "models": models
        }

    except Exception:

        return {
            "online": False,
            "models": []
        }


def ask_ollama(query, results, model=None):

    status = ollama_status()

    if not status["online"]:
        return {
            "success": False,
            "error": (
                "Ollama n'est pas disponible. "
                "Lance Ollama puis vérifie avec "
                "'ollama list'."
            )
        }

    available_models = status["models"]

    selected_model = model or DEFAULT_MODEL

    # Si le modèle demandé n'existe pas,
    # utiliser automatiquement le premier disponible.
    if selected_model not in available_models:

        if available_models:
            selected_model = available_models[0]
        else:
            return {
                "success": False,
                "error": (
                    "Aucun modèle Ollama installé. "
                    "Utilise par exemple : ollama pull llama3.2"
                )
            }

    clean_results = []

    for result in results[:20]:

        clean_results.append({
            "title": clean_text(result.get("title", ""), 300),
            "description": clean_text(
                result.get("description", ""),
                800
            ),
            "url": result.get("url", ""),
            "source": result.get("source", "")
        })

    prompt = f"""
Tu es YTX OSINT, un assistant d'analyse OSINT responsable.

Tu dois analyser UNIQUEMENT les informations publiques
fournies dans les résultats ci-dessous.

REQUÊTE :
{query}

RÉSULTATS :
{json.dumps(clean_results, ensure_ascii=False, indent=2)}

Réponds en français.

Structure ta réponse avec :

1. Résumé
2. Éléments trouvés
3. Sources importantes
4. Recoupements possibles
5. Éléments incertains
6. Conclusion

Règles importantes :

- Ne présente jamais une hypothèse comme un fait.
- Ne fabrique aucune information.
- Ne donne pas d'adresse privée ou de localisation précise.
- Ne recherche pas de mots de passe ou données volées.
- Ne fournis pas de données sensibles.
- Si les sources sont insuffisantes, dis-le clairement.
- Distingue les faits des hypothèses.
"""

    try:

        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": selected_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2
                }
            },
            timeout=AI_TIMEOUT
        )

        if response.status_code != 200:

            return {
                "success": False,
                "error": (
                    f"Ollama a retourné HTTP "
                    f"{response.status_code}"
                )
            }

        data = response.json()

        answer = data.get("response", "").strip()

        if not answer:

            return {
                "success": False,
                "error": "Ollama n'a retourné aucune réponse."
            }

        return {
            "success": True,
            "model": selected_model,
            "answer": answer
        }

    except requests.exceptions.Timeout:

        return {
            "success": False,
            "error": (
                "L'analyse IA prend trop de temps. "
                "Essaie avec une requête plus courte."
            )
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# =========================================================
# RECoupement
# =========================================================

def cross_reference(results):

    sources = {}

    for result in results:

        source = result.get("source", "Inconnu")

        if source not in sources:
            sources[source] = []

        sources[source].append({
            "title": result.get("title", ""),
            "url": result.get("url", "")
        })

    domains = {}

    for result in results:

        url = result.get("url", "")

        match = re.search(
            r"https?://([^/]+)",
            url
        )

        if match:

            domain = match.group(1).lower()

            if domain.startswith("www."):
                domain = domain[4:]

            domains.setdefault(domain, 0)
            domains[domain] += 1

    return {
        "source_count": len(sources),
        "sources": sources,
        "domains": domains,
        "confidence": calculate_confidence(results)
    }


# =========================================================
# RAPPORT
# =========================================================

def generate_report(data, ai_result=None):

    report = {
        "project": "YTX OSINT",
        "version": "2.0",
        "generated_at": now(),
        "query": data.get("query", ""),
        "entities": data.get("entities", []),
        "confidence": data.get("confidence", {}),
        "results": data.get("results", []),
        "cross_reference": cross_reference(
            data.get("results", [])
        ),
        "ai_analysis": ai_result
    }

    return report


# =========================================================
# API — STATUS
# =========================================================

@app.route("/api/status")
def api_status():

    ollama = ollama_status()

    return jsonify({
        "success": True,
        "server": "online",
        "ollama": ollama,
        "history_count": len(history),
        "version": "2.0"
    })


# =========================================================
# API — SEARCH
# =========================================================

@app.route("/api/search", methods=["POST"])
def api_search():

    data = request.get_json(silent=True) or {}

    query = clean_text(
        data.get("query", ""),
        500
    )

    if not query:

        return jsonify({
            "success": False,
            "error": "Recherche vide."
        }), 400

    result = perform_search(query)

    return jsonify({
        "success": True,
        **result
    })


# =========================================================
# API — IA
# =========================================================

@app.route("/api/ai", methods=["POST"])
def api_ai():

    data = request.get_json(silent=True) or {}

    query = clean_text(
        data.get("query", ""),
        500
    )

    results = data.get("results", [])

    model = clean_text(
        data.get("model", ""),
        100
    )

    if not query:

        return jsonify({
            "success": False,
            "error": "Aucune requête fournie."
        }), 400

    if not isinstance(results, list):
        results = []

    result = ask_ollama(
        query,
        results,
        model=model or None
    )

    return jsonify(result)


# =========================================================
# API — RECOUPEMENT
# =========================================================

@app.route("/api/cross-reference", methods=["POST"])
def api_cross_reference():

    data = request.get_json(silent=True) or {}

    results = data.get("results", [])

    if not isinstance(results, list):
        results = []

    result = cross_reference(results)

    return jsonify({
        "success": True,
        **result
    })


# =========================================================
# API — RAPPORT
# =========================================================

@app.route("/api/report", methods=["POST"])
def api_report():

    data = request.get_json(silent=True) or {}

    query = clean_text(
        data.get("query", ""),
        500
    )

    results = data.get("results", [])
    entities = data.get("entities", [])
    confidence = data.get("confidence", {})
    ai_analysis = data.get("ai_analysis")

    report_data = {
        "query": query,
        "results": results,
        "entities": entities,
        "confidence": confidence
    }

    report = generate_report(
        report_data,
        ai_analysis
    )

    return jsonify({
        "success": True,
        "report": report
    })


# =========================================================
# API — HISTORIQUE
# =========================================================

@app.route("/api/history")
def api_history():

    return jsonify({
        "success": True,
        "history": history
    })


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():

    history.clear()

    return jsonify({
        "success": True
    })


# =========================================================
# INTERFACE
# =========================================================

HTML = r"""
<!DOCTYPE html>
<html lang="fr">

<head>

<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>YTX OSINT V2</title>

<style>

*{
    box-sizing:border-box;
    margin:0;
    padding:0;
}

body{
    background:
        radial-gradient(circle at top left,#14213d 0,#080b12 35%,#05070b 100%);
    color:#edf3ff;
    font-family:Arial,Helvetica,sans-serif;
    min-height:100vh;
}

button,
input,
select{
    font:inherit;
}

button{
    cursor:pointer;
}

.app{
    display:flex;
    min-height:100vh;
}

.sidebar{
    width:245px;
    background:rgba(7,10,17,.94);
    border-right:1px solid #202b3e;
    padding:22px 15px;
    position:fixed;
    left:0;
    top:0;
    bottom:0;
}

.logo{
    font-size:25px;
    font-weight:900;
    letter-spacing:1px;
    margin-bottom:7px;
}

.logo span{
    color:#62a7ff;
}

.version{
    color:#697995;
    font-size:12px;
    margin-bottom:30px;
}

.nav button{
    width:100%;
    text-align:left;
    padding:13px 14px;
    border:0;
    background:transparent;
    color:#8f9db6;
    border-radius:10px;
    margin-bottom:6px;
}

.nav button:hover,
.nav button.active{
    background:#111c2e;
    color:white;
}

.status{
    position:absolute;
    left:15px;
    right:15px;
    bottom:20px;
    background:#0d1522;
    border:1px solid #213047;
    border-radius:12px;
    padding:13px;
}

.status-dot{
    display:inline-block;
    width:8px;
    height:8px;
    background:#38e58a;
    border-radius:50%;
    margin-right:7px;
}

.status small{
    color:#72819a;
    display:block;
    margin-top:6px;
}

.main{
    margin-left:245px;
    width:calc(100% - 245px);
    padding:25px;
}

.top{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:25px;
}

.top h1{
    font-size:28px;
}

.badge{
    background:#0d1d30;
    color:#63aaff;
    border:1px solid #1f4e7f;
    border-radius:30px;
    padding:9px 14px;
    font-size:13px;
}

.search{
    display:flex;
    gap:10px;
    background:#0a101a;
    border:1px solid #26364e;
    padding:10px;
    border-radius:15px;
    margin-bottom:20px;
}

.search input{
    flex:1;
    background:transparent;
    border:0;
    outline:none;
    color:white;
    padding:12px;
    font-size:16px;
}

.primary{
    background:#3182f6;
    border:0;
    color:white;
    padding:12px 20px;
    border-radius:10px;
    font-weight:bold;
}

.primary:hover{
    background:#4790f7;
}

.grid{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:12px;
    margin-bottom:20px;
}

.card{
    background:rgba(11,17,28,.88);
    border:1px solid #202e43;
    border-radius:15px;
    padding:17px;
}

.card-title{
    color:#71819b;
    font-size:12px;
    text-transform:uppercase;
    letter-spacing:.8px;
}

.card-value{
    font-size:25px;
    font-weight:bold;
    margin-top:8px;
}

.content{
    display:grid;
    grid-template-columns:1.5fr 1fr;
    gap:15px;
}

.panel{
    background:rgba(9,14,23,.92);
    border:1px solid #202e43;
    border-radius:15px;
    padding:18px;
    margin-bottom:15px;
}

.panel h2{
    font-size:17px;
    margin-bottom:15px;
}

.result{
    border:1px solid #1c2a3e;
    background:#0b121e;
    border-radius:11px;
    padding:14px;
    margin-bottom:10px;
}

.result h3{
    font-size:15px;
    margin-bottom:6px;
}

.result p{
    color:#8796ad;
    line-height:1.5;
    font-size:13px;
}

.result a{
    color:#5fa7ff;
    font-size:12px;
    display:block;
    margin-top:8px;
    word-break:break-all;
}

.actions{
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin-bottom:15px;
}

.action{
    background:#101a29;
    border:1px solid #263a55;
    color:#cbd8eb;
    border-radius:9px;
    padding:9px 12px;
}

.action:hover{
    background:#17253a;
}

.ai{
    white-space:pre-wrap;
    line-height:1.65;
    color:#dce7f8;
    font-size:14px;
}

.confidence{
    display:inline-block;
    border-radius:20px;
    padding:6px 10px;
    background:#10251d;
    color:#53e296;
    font-size:12px;
    margin-bottom:10px;
}

.entity{
    display:inline-block;
    background:#121d2e;
    border:1px solid #253b58;
    padding:7px 9px;
    border-radius:8px;
    margin:3px;
    font-size:12px;
}

.empty{
    color:#66748a;
    text-align:center;
    padding:35px 10px;
}

.history-item{
    border-bottom:1px solid #1d293a;
    padding:12px 0;
}

.history-item:last-child{
    border-bottom:0;
}

.history-item strong{
    display:block;
    margin-bottom:5px;
}

.history-item small{
    color:#687890;
}

.hidden{
    display:none;
}

pre{
    white-space:pre-wrap;
    word-break:break-word;
}

@media(max-width:900px){

    .sidebar{
        width:70px;
        padding:15px 8px;
    }

    .logo{
        font-size:0;
        text-align:center;
    }

    .logo span{
        font-size:22px;
    }

    .version,
    .nav button span,
    .status{
        display:none;
    }

    .nav button{
        text-align:center;
        font-size:18px;
    }

    .main{
        margin-left:70px;
        width:calc(100% - 70px);
        padding:15px;
    }

    .grid{
        grid-template-columns:repeat(2,1fr);
    }

    .content{
        grid-template-columns:1fr;
    }
}

</style>

</head>

<body>

<div class="app">

<aside class="sidebar">

<div class="logo">
YTX <span>OSINT</span>
</div>

<div class="version">
PUBLIC INTELLIGENCE PLATFORM · V2
</div>

<div class="nav">

<button class="active" onclick="showSection('investigation',this)">
🔎 <span>Investigation</span>
</button>

<button onclick="showSection('cross',this)">
🧩 <span>Recoupement</span>
</button>

<button onclick="showSection('ai',this)">
🤖 <span>Analyse IA</span>
</button>

<button onclick="showSection('reports',this)">
📄 <span>Rapports</span>
</button>

<button onclick="showSection('history',this)">
🕘 <span>Historique</span>
</button>

</div>

<div class="status">

<div>
<span class="status-dot"></span>
<span id="ollamaStatus">
Vérification...
</span>
</div>

<small id="modelStatus">
Ollama
</small>

</div>

</aside>

<main class="main">

<div class="top">

<div>
<h1>YTX OSINT</h1>
<p style="color:#718099;margin-top:5px">
Recherche et analyse d'informations publiques
</p>
</div>

<div class="badge">
● SYSTÈME EN LIGNE
</div>

</div>

<section id="investigation">

<div class="search">

<input
id="query"
placeholder="Nom public, pseudo, domaine, entreprise..."
autocomplete="off"
>

<button
class="primary"
onclick="searchOSINT()"
>
RECHERCHER
</button>

</div>

<div class="actions">

<button class="action"
onclick="runAI()">
🤖 Analyser avec IA
</button>

<button class="action"
onclick="runCrossReference()">
🧩 Recouper
</button>

<button class="action"
onclick="generateReport()">
📄 Rapport
</button>

<button class="action"
onclick="exportJSON()">
⬇ JSON
</button>

</div>

<div class="grid">

<div class="card">
<div class="card-title">Résultats</div>
<div class="card-value" id="statResults">0</div>
</div>

<div class="card">
<div class="card-title">Entités</div>
<div class="card-value" id="statEntities">0</div>
</div>

<div class="card">
<div class="card-title">Confiance</div>
<div class="card-value" id="statConfidence">—</div>
</div>

<div class="card">
<div class="card-title">Sources</div>
<div class="card-value" id="statSources">0</div>
</div>

</div>

<div class="content">

<div>

<div class="panel">

<h2>Résultats publics</h2>

<div id="results">

<div class="empty">
Lance une recherche pour commencer.
</div>

</div>

</div>

</div>

<div>

<div class="panel">

<h2>Entités détectées</h2>

<div id="entities">

<div class="empty">
Aucune entité.
</div>

</div>

</div>

<div class="panel">

<h2>Confiance</h2>

<div id="confidence">

<div class="empty">
Pas encore de données.
</div>

</div>

</div>

</div>

</div>

</section>


<section id="cross" class="hidden">

<div class="panel">

<h2>🧩 Recoupement des sources</h2>

<div id="crossData">

<div class="empty">
Lance d'abord une recherche.
</div>

</div>

</div>

</section>


<section id="ai" class="hidden">

<div class="panel">

<h2>🤖 Analyse IA locale</h2>

<div id="aiData">

<div class="empty">
Lance une recherche puis clique sur « Analyser avec IA ».
</div>

</div>

</div>

</section>


<section id="reports" class="hidden">

<div class="panel">

<h2>📄 Rapport d'enquête</h2>

<div id="reportData">

<div class="empty">
Aucun rapport généré.
</div>

</div>

</div>

</section>


<section id="history" class="hidden">

<div class="panel">

<div style="display:flex;justify-content:space-between;align-items:center">

<h2>🕘 Historique</h2>

<button
class="action"
onclick="clearHistory()">
Effacer
</button>

</div>

<div id="historyData">

<div class="empty">
Aucun historique.
</div>

</div>

</div>

</section>

</main>

</div>


<script>

let currentData = null;
let currentAI = null;
let currentCross = null;


function escapeHTML(value){

    if(value === null || value === undefined)
        return "";

    return String(value)
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}


function showSection(name, button){

    document.querySelectorAll("main section")
        .forEach(section => {
            section.classList.add("hidden");
        });

    const target = document.getElementById(name);

    if(target)
        target.classList.remove("hidden");

    document.querySelectorAll(".nav button")
        .forEach(btn => btn.classList.remove("active"));

    if(button)
        button.classList.add("active");

    if(name === "history")
        loadHistory();
}


async function searchOSINT(){

    const input = document.getElementById("query");

    const query = input.value.trim();

    if(!query){
        alert("Entre une recherche.");
        return;
    }

    document.getElementById("results").innerHTML =
        '<div class="empty">Recherche en cours...</div>';

    try{

        const response = await fetch("/api/search",{

            method:"POST",

            headers:{
                "Content-Type":"application/json"
            },

            body:JSON.stringify({
                query:query
            })

        });

        const data = await response.json();

        if(!data.success){
            throw new Error(data.error || "Erreur");
        }

        currentData = data;
        currentAI = null;
        currentCross = null;

        renderData();

    }catch(error){

        document.getElementById("results").innerHTML =
            `<div class="empty">❌ ${escapeHTML(error.message)}</div>`;

    }
}


function renderData(){

    if(!currentData)
        return;

    const results = currentData.results || [];
    const entities = currentData.entities || [];
    const confidence = currentData.confidence || {};

    document.getElementById("statResults").textContent =
        results.length;

    document.getElementById("statEntities").textContent =
        entities.length;

    document.getElementById("statConfidence").textContent =
        confidence.score !== undefined
            ? confidence.score + "%"
            : "—";

    const sources = new Set(
        results
            .map(x => x.source)
            .filter(Boolean)
    );

    document.getElementById("statSources").textContent =
        sources.size;


    document.getElementById("results").innerHTML =
        results.length

        ? results.map(result => `

            <div class="result">

                <h3>
                    ${escapeHTML(
                        result.title || "Résultat"
                    )}
                </h3>

                <p>
                    ${escapeHTML(
                        result.description || ""
                    )}
                </p>

                ${
                    result.url
                    ?
                    `<a href="${escapeHTML(result.url)}"
                        target="_blank"
                        rel="noopener noreferrer">
                        ${escapeHTML(result.url)}
                    </a>`
                    :
                    ""
                }

                <small style="
                    color:#53647c;
                    display:block;
                    margin-top:7px;
                ">
                    ${escapeHTML(result.source || "Source")}
                </small>

            </div>

        `).join("")

        :

        `<div class="empty">
            Aucun résultat public trouvé.
        </div>`;


    document.getElementById("entities").innerHTML =
        entities.length

        ? entities.map(entity => `

            <span class="entity">
                ${escapeHTML(entity.type)}
                :
                ${escapeHTML(entity.value)}
            </span>

        `).join("")

        :

        `<div class="empty">
            Aucune entité détectée.
        </div>`;


    document.getElementById("confidence").innerHTML = `

        <div class="confidence">
            ${escapeHTML(confidence.label || "INCONNUE")}
            ·
            ${escapeHTML(confidence.score || 0)}%
        </div>

        <p style="color:#7d8ba1;line-height:1.5">
            ${escapeHTML(confidence.reason || "")}
        </p>
    `;
}


async function runAI(){

    if(!currentData){

        alert("Lance d'abord une recherche.");

        return;
    }

    showSection(
        "ai",
        document.querySelectorAll(".nav button")[2]
    );

    document.getElementById("aiData").innerHTML =
        '<div class="empty">🤖 Analyse IA en cours...</div>';

    try{

        const response = await fetch("/api/ai",{

            method:"POST",

            headers:{
                "Content-Type":"application/json"
            },

            body:JSON.stringify({

                query:currentData.query,

                results:currentData.results

            })

        });

        const data = await response.json();

        currentAI = data;

        if(!data.success){

            document.getElementById("aiData").innerHTML = `

                <div class="empty">
                    ❌ ${escapeHTML(data.error)}
                </div>

            `;

            return;
        }

        document.getElementById("aiData").innerHTML = `

            <div style="
                color:#6faeff;
                margin-bottom:15px;
                font-size:12px;
            ">
                MODÈLE : ${escapeHTML(data.model)}
            </div>

            <div class="ai">
                ${escapeHTML(data.answer)}
            </div>

        `;

    }catch(error){

        document.getElementById("aiData").innerHTML = `

            <div class="empty">
                ❌ ${escapeHTML(error.message)}
            </div>

        `;

    }
}


async function runCrossReference(){

    if(!currentData){

        alert("Lance d'abord une recherche.");

        return;
    }

    showSection(
        "cross",
        document.querySelectorAll(".nav button")[1]
    );

    document.getElementById("crossData").innerHTML =
        '<div class="empty">🧩 Recoupement en cours...</div>';

    try{

        const response = await fetch(
            "/api/cross-reference",
            {

                method:"POST",

                headers:{
                    "Content-Type":"application/json"
                },

                body:JSON.stringify({
                    results:currentData.results
                })

            }
        );

        const data = await response.json();

        currentCross = data;

        if(!data.success)
            throw new Error(data.error || "Erreur");

        let html = `

            <div class="grid">

                <div class="card">
                    <div class="card-title">
                        Sources
                    </div>
                    <div class="card-value">
                        ${data.source_count}
                    </div>
                </div>

                <div class="card">
                    <div class="card-title">
                        Score
                    </div>
                    <div class="card-value">
                        ${data.confidence.score}%
                    </div>
                </div>

            </div>

            <h3 style="margin-bottom:10px">
                Domaines trouvés
            </h3>
        `;

        const domains = data.domains || {};

        const entries = Object.entries(domains);

        if(entries.length){

            html += entries.map(
                ([domain,count]) => `

                    <div class="result">

                        <h3>
                            ${escapeHTML(domain)}
                        </h3>

                        <p>
                            ${count} résultat(s)
                        </p>

                    </div>

                `
            ).join("");

        }else{

            html += `
                <div class="empty">
                    Aucun domaine détecté.
                </div>
            `;
        }

        html += `
            <h3 style="margin-top:20px;margin-bottom:10px">
                Sources utilisées
            </h3>
        `;

        const sourceEntries =
            Object.entries(data.sources || {});

        html += sourceEntries.map(
            ([source,items]) => `

                <div class="result">

                    <h3>
                        ${escapeHTML(source)}
                    </h3>

                    <p>
                        ${items.length} résultat(s)
                    </p>

                </div>

            `
        ).join("");

        document.getElementById("crossData").innerHTML =
            html;

    }catch(error){

        document.getElementById("crossData").innerHTML =
            `<div class="empty">
                ❌ ${escapeHTML(error.message)}
            </div>`;

    }
}


async function generateReport(){

    if(!currentData){

        alert("Lance d'abord une recherche.");

        return;
    }

    showSection(
        "reports",
        document.querySelectorAll(".nav button")[3]
    );

    document.getElementById("reportData").innerHTML =
        '<div class="empty">📄 Génération...</div>';

    try{

        const response = await fetch(
            "/api/report",
            {

                method:"POST",

                headers:{
                    "Content-Type":"application/json"
                },

                body:JSON.stringify({

                    query:currentData.query,

                    results:currentData.results,

                    entities:currentData.entities,

                    confidence:currentData.confidence,

                    ai_analysis:currentAI

                })

            }
        );

        const data = await response.json();

        if(!data.success)
            throw new Error(data.error || "Erreur");

        const report =
            JSON.stringify(
                data.report,
                null,
                2
            );

        document.getElementById("reportData").innerHTML = `

            <div class="actions">

                <button class="primary"
                    onclick="downloadReport()">
                    ⬇ Télécharger JSON
                </button>

            </div>

            <pre>${escapeHTML(report)}</pre>
        `;

        window.generatedReport = data.report;

    }catch(error){

        document.getElementById("reportData").innerHTML =
            `<div class="empty">
                ❌ ${escapeHTML(error.message)}
            </div>`;

    }
}


function downloadReport(){

    if(!window.generatedReport)
        return;

    const blob = new Blob(
        [
            JSON.stringify(
                window.generatedReport,
                null,
                2
            )
        ],
        {
            type:"application/json"
        }
    );

    const url =
        URL.createObjectURL(blob);

    const a =
        document.createElement("a");

    a.href = url;

    a.download =
        "ytx-osint-report.json";

    document.body.appendChild(a);

    a.click();

    a.remove();

    URL.revokeObjectURL(url);
}


function exportJSON(){

    if(!currentData){

        alert("Aucune recherche à exporter.");

        return;
    }

    const blob = new Blob(
        [
            JSON.stringify(
                currentData,
                null,
                2
            )
        ],
        {
            type:"application/json"
        }
    );

    const url =
        URL.createObjectURL(blob);

    const a =
        document.createElement("a");

    a.href = url;

    a.download =
        "ytx-osint-search.json";

    document.body.appendChild(a);

    a.click();

    a.remove();

    URL.revokeObjectURL(url);
}


async function loadHistory(){

    try{

        const response =
            await fetch("/api/history");

        const data =
            await response.json();

        const container =
            document.getElementById("historyData");

        if(!data.history.length){

            container.innerHTML =
                `<div class="empty">
                    Aucun historique.
                </div>`;

            return;
        }

        container.innerHTML =
            data.history.map(
                item => `

                    <div
                        class="history-item"
                        onclick="restoreHistory(
                            ${JSON.stringify(
                                item
                            ).replaceAll('"',"&quot;")}
                        )"
                        style="cursor:pointer"
                    >

                        <strong>
                            ${escapeHTML(item.query)}
                        </strong>

                        <small>
                            ${escapeHTML(item.timestamp)}
                            ·
                            ${item.results.length}
                            résultat(s)
                        </small>

                    </div>

                `
            ).join("");

    }catch(error){

        console.error(error);

    }
}


function restoreHistory(item){

    currentData = item;

    document.getElementById("query").value =
        item.query;

    showSection(
        "investigation",
        document.querySelectorAll(".nav button")[0]
    );

    renderData();
}


async function clearHistory(){

    await fetch(
        "/api/history/clear",
        {
            method:"POST"
        }
    );

    loadHistory();
}


async function checkStatus(){

    try{

        const response =
            await fetch("/api/status");

        const data =
            await response.json();

        const ollama =
            data.ollama;

        const status =
            document.getElementById(
                "ollamaStatus"
            );

        const model =
            document.getElementById(
                "modelStatus"
            );

        if(ollama.online){

            status.textContent =
                "IA locale disponible";

            model.textContent =
                ollama.models.length
                ? ollama.models.join(", ")
                : "Aucun modèle installé";

        }else{

            status.textContent =
                "IA locale hors ligne";

            model.textContent =
                "Lance Ollama";

        }

    }catch(error){

        document.getElementById(
            "ollamaStatus"
        ).textContent =
            "Serveur hors ligne";

    }
}


document
    .getElementById("query")
    .addEventListener(
        "keydown",
        event => {

            if(event.key === "Enter"){
                searchOSINT();
            }

        }
    );


checkStatus();

setInterval(
    checkStatus,
    10000
);

</script>

</body>

</html>
"""


# =========================================================
# PAGE PRINCIPALE
# =========================================================

@app.route("/")
def index():

    return Response(
        HTML,
        mimetype="text/html"
    )


# =========================================================
# LANCEMENT
# =========================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("                 YTX OSINT V2")
    print("=" * 60)
    print()
    print(f"🌐 Interface : http://{HOST}:{PORT}")
    print(f"🤖 Ollama    : {OLLAMA_URL}")
    print()
    print("Vérification Ollama...")

    status = ollama_status()

    if status["online"]:

        print("✅ Ollama est connecté.")

        if status["models"]:

            print(
                "🧠 Modèles : "
                + ", ".join(status["models"])
            )

        else:

            print(
                "⚠️ Aucun modèle installé."
            )

            print(
                "   Exemple : ollama pull llama3.2"
            )

    else:

        print("⚠️ Ollama n'est pas détecté.")
        print(
            "   L'interface fonctionnera quand même."
        )

    print()
    print("=" * 60)
    print()

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        load_dotenv=False
    )
