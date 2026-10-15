#!/usr/bin/env python3
"""Coletor de clipping da Unifesspa (várias fontes, sem chave de acesso e sem custo).

Fontes:
  google    Google Notícias (busca por termo, em janelas de dias)
  bing      Bing Notícias (só devolve o último mês; serve para a rotina diária)
  gdelt     GDELT (base aberta de notícias; cobre cerca de 90 dias)
  locais    Busca no próprio site dos veículos locais (API do WordPress, ou o feed da pesquisa)
  sites     Google Notícias restrito a sites sem feed (DOL Carajás, Gazeta Carajás, G1 Pará)
  feeds     Feed geral dos veículos locais: tudo o que publicaram, filtrado aqui dentro
  sitemaps  Sitemap de notícias (últimas 48h) de veículos que não têm feed

Toda matéria nova é lida antes de entrar no clipping: a que não cita a universidade fica marcada
como descartada (não some, dá para ver e restaurar na página). A leitura também serve para guardar
o endereço real da matéria, a data que o site declara e o começo do texto.

Uso:
  python coletar.py --inicio 2026-01-01 --fim 2026-10-05      # histórico por período
  python coletar.py --dias 7                                  # rotina diária
  python coletar.py --reverificar                             # relê o que já está salvo, sem coletar
  python coletar.py --dias 30 --fontes bing,gdelt             # só algumas fontes
  python teste_coletar.py                                     # testes (não acessam a internet)

Os resultados ficam em dados/clipping.json e dados/clipping.csv. Rodar de novo
acrescenta o que for novo e não duplica o que já existe.
"""
import argparse, base64, csv, gzip, html, json, os, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---- EDITE AQUI ------------------------------------------------------------
TERMOS = [
    "Unifesspa",
    "Universidade Federal do Sul e Sudeste do Pará",
    "Francisco Ribeiro da Costa",
    "Lucélia Cardoso Cavalcante",
    # Variações como a imprensa costuma citar (nome curto + cargo/universidade, para não trazer homônimos):
    '"Francisco Ribeiro" reitor Unifesspa',
    '"Lucélia Cavalcante" Unifesspa',
]
# Termos usados nas fontes que aceitam poucas consultas (Bing, GDELT, sites locais).
TERMOS_BASE = TERMOS[:4]
# Termos usados na busca restrita a sites sem feed.
TERMOS_SITES = TERMOS[:2]

# Veículos locais com busca própria do WordPress (endereço do site -> nome no clipping).
# Para incluir outro veículo que use WordPress, acrescente uma linha.
LOCAIS_WORDPRESS = {
    "https://www.zedudu.com.br": "Zé Dudu",
    "https://correiodecarajas.com.br": "Correio de Carajás",
    "https://portalcanaa.com.br": "Portal Canaã",
    "https://portalpebao.com.br": "Portal Pebão",
    "https://hiroshibogea.com.br": "Blog do Hiroshi Bogéa",
    "https://debatecarajas.com.br": "Portal Debate Carajás",
    "https://pebinhadeacucar.com.br": "Pebinha de Açúcar",
    "https://fatoregional.com.br": "Fato Regional",
    "https://portalamazonia.com": "Portal Amazônia",
    "https://www.carajasojornal.com.br": "Carajás O Jornal",
    "https://www.infopebas.com": "Portal Infopebas",
    "https://chocopeba.com.br": "Portal Chocopeba",
    "http://www.an10.com.br": "Jornal A Notícia (AN10)",
    "https://sudesteparaense.com": "Sudeste Paraense",
    "https://www.canaanoticias.com.br": "Canaã Notícias",
}
# Veículos sem feed: busca no Google Notícias e no Bing restrita ao site (domínio -> nome).
LOCAIS_SEM_FEED = {
    "gazetacarajas.com": "Gazeta Carajás",
    "dol.com.br": "DOL Carajás",
    "g1.globo.com/pa": "G1 Pará",
    # Sites que não permitem leitura automática direta (robots.txt) ou sem feed: só pela busca do Google e do Bing.
    "rondon-noticias.unifesspa.edu.br": "Rondon Notícias",
    "sindtifes.org.br": "Sindtifes-PA",
    "adunifesspa.org.br": "ADUNIFESSPA",
    "blogdobranco.com": "Blog do Branco",
    "carajasemfoco.com.br": "Carajás em Foco",
    "xinguaraativa.com.br": "Xinguara Ativa",
    "plantaocanaa.com.br": "Plantão Canaã",
    "romanews.com.br": "Roma News",
    "agenciapara.com.br": "Agência Pará",
    "oliberal.com": "O Liberal",
    "estadodoparaonline.com": "Estado do Pará Online",
    "rbtv.com.br": "Rede Brasil de Televisão",
    "diariodopara.com.br": "Diário do Pará",
}
# Quando o domínio aparece em qualquer fonte, o veículo recebe sempre o mesmo nome.
NOMES_POR_DOMINIO = {
    "zedudu.com.br": "Zé Dudu",
    "correiodecarajas.com.br": "Correio de Carajás",
    "portalcanaa.com.br": "Portal Canaã",
    "portalpebao.com.br": "Portal Pebão",
    "gazetacarajas.com": "Gazeta Carajás",
    "hiroshibogea.com.br": "Blog do Hiroshi Bogéa",
    "debatecarajas.com.br": "Portal Debate Carajás",
    "pebinhadeacucar.com.br": "Pebinha de Açúcar",
    "fatoregional.com.br": "Fato Regional",
    "portalamazonia.com": "Portal Amazônia",
    "rondon-noticias.unifesspa.edu.br": "Rondon Notícias",
    "sindtifes.org.br": "Sindtifes-PA",
    "adunifesspa.org.br": "ADUNIFESSPA",
    "blogdobranco.com": "Blog do Branco",
    "carajasemfoco.com.br": "Carajás em Foco",
    "carajasojornal.com.br": "Carajás O Jornal",
    "infopebas.com": "Portal Infopebas",
    "chocopeba.com.br": "Portal Chocopeba",
    "an10.com.br": "Jornal A Notícia (AN10)",
    "sudesteparaense.com": "Sudeste Paraense",
    "canaanoticias.com.br": "Canaã Notícias",
    "xinguaraativa.com.br": "Xinguara Ativa",
    "plantaocanaa.com.br": "Plantão Canaã",
    "romanews.com.br": "Roma News",
    "agenciapara.com.br": "Agência Pará",
    "oliberal.com": "O Liberal",
    "estadodoparaonline.com": "Estado do Pará Online",
    "rbtv.com.br": "Rede Brasil de Televisão",
    "diariodopara.com.br": "Diário do Pará",
    "dol.com.br": "DOL Carajás",
}
# Alertas do Google entregues em feed RSS. Criados na conta natalia.travassos@unifesspa.edu.br,
# em google.com/alerts, com "Enviar para: Feed RSS" e "Quantos: Ver todos". Para acrescentar um termo,
# crie o alerta lá e cole aqui o endereço do feed (o ícone de RSS ao lado do alerta).
ALERTAS_RSS = {
    "https://www.google.com/alerts/feeds/00735056655681025845/16891666913238257414": "Unifesspa",
    "https://www.google.com/alerts/feeds/00735056655681025845/13212456898336066722":
        "Universidade Federal do Sul e Sudeste do Pará",
}
# Sitemap de notícias (lista o que o site publicou nas últimas 48h, com título e data).
# Serve para veículos sem feed, que só apareciam pela busca do Google.
SITEMAPS_NOTICIAS = {
    "https://www.gazetacarajas.com/sitemap-news.xml": "Gazeta Carajás",
}
# Veículos próprios da universidade: não entram no clipping (é o que a imprensa publica sobre ela).
DOMINIOS_IGNORADOS = ["unifesspa.edu.br"]
# Exceções: veículos de dentro da universidade que a ASCOM quer acompanhar mesmo assim.
DOMINIOS_PERMITIDOS = ["rondon-noticias.unifesspa.edu.br"]
LIMITE_FEED = 100        # o Google devolve no máximo ~100 itens por consulta
LIMITE_GDELT = 250       # máximo de artigos por consulta do GDELT
PAUSA = 1.5              # segundos entre consultas, para não sobrecarregar o serviço
PAUSA_GDELT = 6          # o GDELT pede no mínimo 5 segundos entre consultas
MAX_PAGINAS_WP = 40      # páginas de 10 itens por termo e veículo local
DIAS_GDELT = 90          # janela que o GDELT consegue consultar
DIAS_BING = 30           # janela que o Bing Notícias consegue consultar
MAX_PAGINAS_FEED = 2       # páginas do feed geral de cada veículo local
LIMITE_LEITURA_EXTRA = 25  # matérias lidas por consulta de feed/sitemap para decidir se citam a universidade
LIMITE_CACHE = 6000        # endereços guardados no cache de leitura
CACHE_VERSAO = 2           # mudou o jeito de ler a página: o que foi lido pela regra antiga é lido de novo
FONTES = ["google", "bing", "gdelt", "locais", "sites", "feeds", "sitemaps", "alertas"]
# Verificação do texto: a busca (principalmente a do Google, que aceita variações como "Unifesp") traz
# matérias que não falam da universidade. Antes de entrar no clipping, o texto da matéria é lido e
# conferido. O que não cita a Unifesspa fica marcado como "descartada" (não some: dá para ver e restaurar
# na página, marcando "Mostrar matérias descartadas").
VERIFICAR_TEXTO = True
PAUSA_VERIFICACAO = 1.0
# Aparece no texto da matéria (comparado sem acento e sem maiúsculas).
PADRAO_UNIVERSIDADE = re.compile(r"\bunifesspa\b|universidade federal do sul e sudeste do para")
# Nomes de dirigentes só valem quando a universidade também aparece: sozinho, um nome pega homônimo
# (há outros Francisco Ribeiro da Costa pelo Brasil) e a matéria entraria sem ser da Unifesspa.
PADRAO_DIRIGENTES = re.compile(
    r"\bfrancisco ribeiro da costa\b|\blucelia cardoso cavalcante\b|\blucelia cavalcante\b")

def mencoes(texto):
    """Quantas vezes a universidade é citada. Nome de dirigente conta junto, nunca sozinho."""
    n = len(PADRAO_UNIVERSIDADE.findall(texto))
    return n + len(PADRAO_DIRIGENTES.findall(texto)) if n else 0

def cita(texto):
    return mencoes(texto) > 0
# Endereços que não são matéria: páginas de tag, de categoria, de autor e listas paginadas, que os
# alertas do Google às vezes devolvem ("Arquivos IEEE", "Página: 6940").
PADRAO_NAO_MATERIA = re.compile(
    r"/(tag|tags|categoria|category|autor|author|page|pagina|arquivo|arquivos|busca|search|feed)(/|$)"
    r"|[?&]s=", re.I)
# Títulos de matérias que só listam locais de votação etc.: a Unifesspa aparece apenas como endereço.
PADRAO_INCIDENTAL = re.compile(
    r"zona eleitoral|locais? de votacao|resultado das eleicoes|apuracao|boca de urna|"
    r"confira a agenda dos presidenciaveis")
# ----------------------------------------------------------------------------

PASTA = Path(__file__).parent / "dados"
JSON_PATH, CSV_PATH = PASTA / "clipping.json", PASTA / "clipping.csv"
CACHE_PATH, RESUMO_PATH = PASTA / "cache_textos.json", PASTA / "ultima_coleta.json"
VEICULOS_PATH = PASTA / "veiculos_sem_nome.json"
UA = "Mozilla/5.0 (compatible; ClippingASCOM/1.0)"


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return descompactar(r.read())

def descompactar(dados):
    """Alguns servidores mandam a página compactada mesmo sem ser pedido (foi o caso do Metrópoles).
    Sem desfazer isso, o texto vira lixo binário e a verificação decide no escuro."""
    if dados[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(dados)
        except Exception:
            return dados
    return dados


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def dominio_de(url):
    """Domínio sem 'www.' de um endereço (vazio se não for possível)."""
    try:
        h = urllib.parse.urlparse(url if "//" in url else "//" + url).netloc.lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def data_rfc(txt):
    try:
        return parsedate_to_datetime(txt).date().isoformat()
    except Exception:
        return ""


def _item(titulo, veiculo, dominio, data, link, resumo=""):
    dom = dominio_de(dominio) if dominio else ""
    return {"titulo": (titulo or "").strip(), "veiculo": (veiculo or "").strip(),
            "dominio": dom, "data": data, "link": (link or "").strip(),
            "resumo": limpar_resumo(resumo)}

def limpar_resumo(txt, limite=240):
    """Primeiras linhas da matéria, sem marcação, para a jornalista classificar o tom sem abrir o link."""
    txt = html.unescape(re.sub(r"(?s)<[^>]+>", " ", txt or ""))
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > limite:
        txt = txt[:limite].rsplit(" ", 1)[0] + "..."
    return txt


# ---- Fonte 1: Google Notícias ----------------------------------------------
def consulta(termo, ini, fim, baixar_fn=baixar, site=None):
    """Itens do feed para um termo entre ini (inclusive) e fim (exclusive).
    Com site, restringe a busca a esse site (ex.: dol.com.br)."""
    base = termo if '"' in termo else f'"{termo}"'   # termo com aspas próprias é usado como foi escrito
    q = f'{base}' + (f' site:{site}' if site else '') + f' after:{ini.isoformat()} before:{fim.isoformat()}'
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})
    raiz = ET.fromstring(baixar_fn(url))
    itens = []
    for it in raiz.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        src = it.find("source")
        veiculo = (src.text or "").strip() if src is not None else ""
        dominio = (src.get("url") or "") if src is not None else ""
        if veiculo and titulo.endswith(" - " + veiculo):
            titulo = titulo[: -len(veiculo) - 3].strip()
        itens.append(_item(titulo, veiculo, dominio, data_rfc(it.findtext("pubDate")),
                           it.findtext("link")))
    return itens


def janelas(ini, fim, passo):
    d = ini
    while d <= fim:
        yield d, min(d + timedelta(days=passo), fim + timedelta(days=1))
        d += timedelta(days=passo)


def coletar_janela(termo, ini, fim, baixar_fn, avisos, site=None):
    itens = consulta(termo, ini, fim, baixar_fn, site)
    time.sleep(PAUSA if baixar_fn is baixar else 0)
    if len(itens) >= LIMITE_FEED and (fim - ini).days > 1:
        meio = ini + timedelta(days=(fim - ini).days // 2)
        return (coletar_janela(termo, ini, meio, baixar_fn, avisos, site)
                + coletar_janela(termo, meio, fim, baixar_fn, avisos, site))
    if len(itens) >= LIMITE_FEED:
        avisos.append(f"{termo} em {ini}: pode haver mais itens que o limite do feed")
    return itens


# ---- Fonte 2: Bing Notícias --------------------------------------------------
def consulta_bing(termo, intervalo, baixar_fn=baixar, site=None):
    """Bing Notícias em RSS. intervalo: 8 = últimos 7 dias, 9 = últimos 30 dias."""
    base = termo if '"' in termo else f'"{termo}"'
    q = base + (f' site:{site}' if site else '')
    url = "https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": q, "format": "rss", "setlang": "pt-BR", "cc": "BR", "qft": f'interval="{intervalo}"'})
    raiz = ET.fromstring(baixar_fn(url))
    itens = []
    for it in raiz.iter("item"):
        link = (it.findtext("link") or "").strip()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        real = qs.get("url", [link])[0]                   # o Bing embrulha o endereço real em 'url='
        veiculo = ""
        for ch in it:
            if ch.tag.split("}")[-1] == "Source":
                veiculo = (ch.text or "").strip()
        itens.append(_item((it.findtext("title") or "").strip(), veiculo, real,
                           data_rfc(it.findtext("pubDate")), real, it.findtext("description") or ""))
    return itens


# ---- Fonte 3: GDELT ----------------------------------------------------------
def consulta_gdelt(termo, ini, fim, baixar_fn=baixar):
    """Artigos em português que citam o termo, entre ini (inclusive) e fim (exclusive)."""
    base = termo if '"' in termo else f'"{termo}"'
    q = f"{base} sourcelang:portuguese"
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
        "query": q, "mode": "artlist", "format": "json", "maxrecords": LIMITE_GDELT, "sort": "datedesc",
        "startdatetime": ini.strftime("%Y%m%d") + "000000",
        "enddatetime": (fim - timedelta(days=1)).strftime("%Y%m%d") + "235959"})
    corpo = baixar_fn(url).decode("utf-8", "ignore").strip()
    if corpo.startswith("Please limit") or corpo.startswith("<"):   # limite de consultas por segundo
        time.sleep(PAUSA_GDELT if baixar_fn is baixar else 0)
        corpo = baixar_fn(url).decode("utf-8", "ignore").strip()
    if not corpo:
        return []
    dados = json.loads(corpo)
    itens = []
    for a in dados.get("articles", []):
        sd = a.get("seendate", "")
        data = f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        itens.append(_item(a.get("title"), a.get("domain"), a.get("domain") or a.get("url"),
                           data, a.get("url")))
    return itens


def coletar_gdelt(termo, ini, fim, baixar_fn, avisos):
    itens = consulta_gdelt(termo, ini, fim, baixar_fn)
    time.sleep(PAUSA_GDELT if baixar_fn is baixar else 0)
    if len(itens) >= LIMITE_GDELT and (fim - ini).days > 1:
        meio = ini + timedelta(days=(fim - ini).days // 2)
        return (coletar_gdelt(termo, ini, meio, baixar_fn, avisos)
                + coletar_gdelt(termo, meio, fim, baixar_fn, avisos))
    return itens


# ---- Fonte 4: busca própria de veículos locais (WordPress) ------------------
def consulta_wordpress(site, nome, termo, ini, baixar_fn=baixar):
    """Resultados da busca do próprio site (feed da pesquisa do WordPress), do mais novo para o mais antigo,
    até chegar a ini. A busca do site já filtra pelo termo."""
    base = termo if '"' in termo else f'"{termo}"'
    itens, vistos = [], set()
    for pag in range(1, MAX_PAGINAS_WP + 1):
        url = f"{site}/?" + urllib.parse.urlencode({"s": base, "feed": "rss2", "paged": pag})
        try:
            raiz = ET.fromstring(baixar_fn(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:          # página além do fim dos resultados
                break
            raise
        pagina = []
        for it in raiz.iter("item"):
            pagina.append(_item((it.findtext("title") or "").strip(), nome, it.findtext("link"),
                                data_rfc(it.findtext("pubDate")), it.findtext("link"),
                                it.findtext("description") or ""))
        pagina = [x for x in pagina if x["link"] not in vistos]
        if not pagina:   # fim dos resultados, ou site que ignora a paginação e repete a página
            break
        vistos.update(x["link"] for x in pagina)
        itens += pagina
        datas = [x["data"] for x in pagina if x["data"]]
        if datas and min(datas) < ini.isoformat():
            break
        time.sleep(PAUSA if baixar_fn is baixar else 0)
    return itens


# ---- Fonte 4b: API de busca do WordPress ------------------------------------
def consulta_wp_rest(site, nome, termo, ini, baixar_fn=baixar):
    """Busca pela API do WordPress (/wp-json), mais confiável que o feed da pesquisa e já paginada.
    Devolve [] quando o site não tem a API aberta, e aí vale a busca antiga."""
    base = termo.strip('"')
    itens, vistos = [], set()
    for pag in range(1, MAX_PAGINAS_WP + 1):
        url = f"{site}/wp-json/wp/v2/posts?" + urllib.parse.urlencode(
            {"search": base, "per_page": 20, "page": pag, "orderby": "date",
             "_fields": "link,title,date,excerpt"})
        try:
            dados = json.loads(baixar_fn(url).decode("utf-8", "ignore"))
        except Exception:
            break
        if not isinstance(dados, list) or not dados:
            break
        antes = len(itens)
        for a in dados:
            titulo = html.unescape(re.sub(r"(?s)<[^>]+>", " ", (a.get("title") or {}).get("rendered", "")))
            if a.get("link") in vistos:
                continue
            vistos.add(a.get("link"))
            itens.append(_item(titulo.strip(), nome, a.get("link"), (a.get("date") or "")[:10],
                               a.get("link"), (a.get("excerpt") or {}).get("rendered", "")))
        if len(itens) == antes:   # página repetida
            break
        datas = [x["data"] for x in itens if x["data"]]
        if datas and min(datas) < ini.isoformat():
            break
        time.sleep(PAUSA if baixar_fn is baixar else 0)
    return itens


def consulta_local(site, nome, termo, ini, baixar_fn=baixar):
    """Busca no próprio veículo: tenta a API do WordPress e, se não houver, o feed da pesquisa."""
    itens = consulta_wp_rest(site, nome, termo, ini, baixar_fn)
    return itens if itens else consulta_wordpress(site, nome, termo, ini, baixar_fn)


# ---- Fonte 6: feed geral dos veículos locais --------------------------------
def consulta_feed(site, nome, ini, baixar_fn=baixar):
    """Tudo o que o veículo publicou recentemente (feed geral), filtrado aqui dentro. Não depende da
    busca do site nem de o Google ter indexado a matéria."""
    itens, vistos = [], set()
    for pag in range(1, MAX_PAGINAS_FEED + 1):
        url = f"{site}/feed/" + (f"?paged={pag}" if pag > 1 else "")
        try:
            raiz = ET.fromstring(baixar_fn(url))
        except Exception:
            break
        pagina = [_item((it.findtext("title") or "").strip(), nome, it.findtext("link"),
                        data_rfc(it.findtext("pubDate")), it.findtext("link"),
                        it.findtext("description") or "")
                  for it in raiz.iter("item")]
        pagina = [x for x in pagina if x["link"] not in vistos]
        if not pagina:
            break
        vistos.update(x["link"] for x in pagina)
        itens += pagina
        if [x["data"] for x in pagina if x["data"]] and min(x["data"] for x in pagina if x["data"]) < ini.isoformat():
            break
        time.sleep(PAUSA if baixar_fn is baixar else 0)
    recentes = [x for x in itens if x["data"] >= ini.isoformat()]
    return filtrar_mencao(recentes, baixar_fn)


# ---- Fonte 7: sitemap de notícias -------------------------------------------
NS_MAPA = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NS_NOTICIA = "{http://www.google.com/schemas/sitemap-news/0.9}"

def consulta_sitemap_news(endereco, nome, baixar_fn=baixar):
    """Matérias das últimas 48h declaradas pelo próprio site no sitemap de notícias, com título e data.
    É o caminho para veículos sem feed, que hoje só aparecem se o Google indexar."""
    raiz = ET.fromstring(baixar_fn(endereco))
    itens = []
    for u in raiz.iter(NS_MAPA + "url"):
        link = (u.findtext(NS_MAPA + "loc") or "").strip()
        noticia = u.find(NS_NOTICIA + "news")
        titulo = (noticia.findtext(NS_NOTICIA + "title") or "").strip() if noticia is not None else ""
        data = (noticia.findtext(NS_NOTICIA + "publication_date") or "")[:10] if noticia is not None else ""
        if link and titulo:
            itens.append(_item(titulo, nome, link, data, link))
    return filtrar_mencao(itens, baixar_fn)


# ---- Fonte 8: alertas do Google (entrega em feed RSS) -----------------------
NS_ATOM = "{http://www.w3.org/2005/Atom}"

def eh_materia(link):
    """Falso para página de tag, de categoria, de autor e lista paginada, que não são matéria."""
    try:
        caminho = urllib.parse.urlsplit(link)
    except Exception:
        return True
    return not PADRAO_NAO_MATERIA.search(caminho.path + "?" + caminho.query)


def consulta_alerta(endereco, termo, baixar_fn=baixar):
    """Matérias de um alerta do Google. O alerta já entrega o endereço real do veículo, a data e um
    trecho do texto, e alcança veículos que ninguém mapeou aqui."""
    raiz = ET.fromstring(baixar_fn(endereco))
    itens = []
    for e in raiz.iter(NS_ATOM + "entry"):
        marcado = e.findtext(NS_ATOM + "title") or ""
        titulo = re.sub(r"(?s)<[^>]+>", "", html.unescape(marcado)).strip()
        alvo = e.find(NS_ATOM + "link")
        bruto = alvo.get("href") if alvo is not None else ""
        try:   # o alerta embrulha o endereço real em 'url='
            link = urllib.parse.parse_qs(urllib.parse.urlsplit(bruto).query).get("url", [""])[0]
        except Exception:
            link = ""
        data = (e.findtext(NS_ATOM + "published") or "")[:10]
        if titulo and link and eh_materia(link):
            itens.append(_item(titulo, "", link, data, link, e.findtext(NS_ATOM + "content") or ""))
    return itens


# ---- Verificação do texto da matéria ----------------------------------------
# Cada matéria é lida uma única vez; o resultado fica guardado em dados/cache_textos.json,
# para que rodar de novo não custe nada.
CACHE_TEXTOS = {}

def carregar_cache():
    global CACHE_TEXTOS
    try:
        CACHE_TEXTOS = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    except Exception:
        CACHE_TEXTOS = {}
    return CACHE_TEXTOS

def salvar_cache():
    PASTA.mkdir(exist_ok=True)
    itens = list(CACHE_TEXTOS.items())[-LIMITE_CACHE:]
    CACHE_PATH.write_text(json.dumps(dict(itens), ensure_ascii=False), encoding="utf-8")

def limpar_url(url):
    """Endereço sem rastreadores e sem barra no fim, para comparar duas versões do mesmo link."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return url or ""
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith(("utm_", "fbclid", "gclid", "amp"))]
    caminho = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), caminho, urllib.parse.urlencode(q), ""))

def endereco_google(link, baixar_fn=baixar):
    """Endereço real de uma matéria cujo link é do Google Notícias (que só redireciona pelo navegador).
    Devolve '' se não conseguir."""
    try:
        id_ = urllib.parse.urlparse(link).path.rstrip("/").split("/")[-1]
        bruto = base64.urlsafe_b64decode(id_ + "=" * (-len(id_) % 4))
        # Formato antigo: o endereço vem escrito dentro do próprio código.
        m = re.search(rb"https?://[\x21-\x7e]+", bruto)
        if m and b"AU_yqL" not in bruto:
            return m.group(0).decode("ascii", "ignore")
        # Formato novo: pergunta ao próprio Google (mesma chamada que o navegador faz).
        pag = baixar_fn(f"https://news.google.com/rss/articles/{id_}").decode("utf-8", "ignore")
        ts = re.search(r'data-n-a-ts="(\d+)"', pag)
        sg = re.search(r'data-n-a-sg="([^"]+)"', pag)
        if not (ts and sg):
            return ""
        carga = ["Fbv4je", json.dumps(
            ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "BR:pt-419", None, 1, None, None, None,
                             None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             id_, int(ts.group(1)), sg.group(1)]), None, "generic"]
        corpo = urllib.parse.urlencode({"f.req": json.dumps([[carga]])}).encode()
        req = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=corpo,
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = r.read().decode("utf-8", "ignore")
        bloco = json.loads(resp.split("\n\n", 1)[1])
        return json.loads(bloco[0][2])[1]
    except Exception:
        return ""

def _sem_enfeite(pag, estrito):
    """Tira scripts e navegação. No modo estrito tira também cabeçalho e barra lateral, onde ficam
    menus e caixas de "veja também"; no modo largo eles ficam, porque é lá que às vezes está a citação."""
    tags = "script|style|noscript|svg|nav|footer|form" + ("|header|aside" if estrito else "")
    pag = re.sub(r"(?is)<(" + tags + r")\b.*?</\1>", " ", pag)
    return re.sub(r"(?is)<!--.*?-->", " ", pag)

def texto_do_html(pag, estrito=True):
    """Texto da página inteira, fora os enfeites. É nele que se procura a menção: a universidade
    costuma aparecer em lista ou tabela, que ficam fora de <article>."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", _sem_enfeite(pag, estrito)))).strip()

def texto_principal(pag):
    """Só o corpo da matéria, para servir de resumo."""
    corpo = _sem_enfeite(pag, True)
    m = re.search(r"(?is)<article\b.*?</article>", corpo) or re.search(r"(?is)<main\b.*?</main>", corpo)
    if m:
        corpo = m.group(0)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", corpo))).strip()

def leu_de_verdade(titulo, texto):
    """A página lida é mesmo a matéria? Se o próprio título não aparece no texto, o que veio foi aviso
    de assinatura, página de erro ou lixo, e aí não dá para afirmar que a matéria não cita a universidade."""
    palavras = [p for p in norm(titulo).split() if len(p) > 3]
    if not palavras:
        return True
    achadas = sum(1 for p in palavras if p in texto)
    return achadas >= max(2, int(len(palavras) * 0.6))

def data_do_html(pag):
    """Data que a própria matéria declara (aaaa-mm-dd), ou '' se não houver.
    O GDELT grava a data em que viu a matéria, não a da publicação; esta corrige."""
    for padrao in (r'article:published_time"[^>]*content="([^"]+)"',
                   r'content="([^"]+)"[^>]*property="article:published_time"',
                   r'"datePublished"\s*:\s*"([^"]+)"',
                   r'<time[^>]+datetime="([^"]+)"'):
        m = re.search(padrao, pag, re.I)
        if not m:
            continue
        d = re.match(r"(\d{4})-(\d{2})-(\d{2})", m.group(1).strip())
        if d:
            try:
                achada = date.fromisoformat(d.group(0))
            except ValueError:
                continue
            if date(2000, 1, 1) <= achada <= date.today() + timedelta(days=1):
                return achada.isoformat()
    return ""

def ler_artigo(link, titulo="", baixar_fn=baixar):
    """Lê a matéria (uma vez só) e devolve o endereço real, o nº de menções, a data e o começo do texto."""
    guardado = CACHE_TEXTOS.get(link)
    if guardado and guardado.get("v") == CACHE_VERSAO:
        return guardado
    final = link
    if "news.google.com" in dominio_de(link):
        final = endereco_google(link, baixar_fn) or ""
    r = {"final": limpar_url(final), "m": 0, "pub": "", "ini": "", "ok": False, "v": CACHE_VERSAO}
    if final:
        try:
            bruto = descompactar(baixar_fn(final))
            try:
                pag = bruto.decode("utf-8")
            except UnicodeDecodeError:
                pag = bruto.decode("latin-1")
        except Exception:
            pag = ""
        corpo = norm(texto_do_html(pag, True)) if pag else ""
        todo = norm(texto_do_html(pag, False)) if pag else ""
        n = mencoes(corpo)
        # Página vazia, bloqueio, muro de assinatura ou lixo compactado: não dá para concluir nada.
        # Com menção achada, a leitura serviu, mesmo que o resto da página tenha vindo capado.
        if len(todo) >= 300 and (n or leu_de_verdade(titulo, todo)):
            r = {"final": limpar_url(final), "m": n, "pub": data_do_html(pag),
                 "ini": limpar_resumo(texto_principal(pag) or texto_do_html(pag, False)), "ok": True,
                 "margem": bool(not n and cita(todo)), "v": CACHE_VERSAO}
    CACHE_TEXTOS[link] = r
    return r

def filtrar_mencao(itens, baixar_fn=baixar, limite=LIMITE_LEITURA_EXTRA):
    """Fica só com as matérias que citam a universidade. Decide pelo título e pelo resumo; quando esses
    não bastam, lê o texto (até `limite` leituras novas por consulta; as já lidas antes não contam)."""
    fica, lidas = [], 0
    for it in itens:
        if cita(norm(it["titulo"] + " " + it.get("resumo", ""))):
            fica.append(it)
            continue
        if it["link"] not in CACHE_TEXTOS:
            if lidas >= limite:
                continue
            lidas += 1
            time.sleep(PAUSA_VERIFICACAO if baixar_fn is baixar else 0)
        r = ler_artigo(it["link"], it["titulo"], baixar_fn)
        if r["ok"] and r["m"]:
            fica.append(it)
    return fica

def verificar(it, baixar_fn=baixar):
    """Confere se a matéria cita a Unifesspa. Devolve (situação, leitura):
    'sim', 'nao' (leu o texto e não achou) ou '?' (não foi possível ler a matéria)."""
    n_titulo = mencoes(norm(it["titulo"]))
    r = ler_artigo(it["link"], it["titulo"], baixar_fn)
    if n_titulo and not r["ok"]:
        return "sim", {"final": "", "m": n_titulo, "pub": "", "ini": "", "ok": False, "v": CACHE_VERSAO}
    if not r["ok"]:
        return "?", r
    if r["m"] or n_titulo:
        return "sim", r
    if r.get("margem"):   # aparece só no menu ou no "veja também": não conta, mas não descarta
        return "?", r
    return "nao", r

def aplicar_verificacao(reg, baixar_fn=baixar):
    """Confere a matéria e aproveita a leitura para guardar o endereço real (no lugar do redirecionamento
    do Google), a data que o site declara e o começo do texto. Só descarta quando leu a matéria e a
    universidade não aparece, ou quando é menção de passagem em lista de locais de votação e agenda."""
    situacao, r = verificar(reg, baixar_fn)
    reg["verificada"] = situacao != "?"
    reg["mencoes"] = r["m"]
    if r.get("final") and "news.google.com" not in r["final"]:
        reg["link"] = r["final"]
        reg["dominio"] = dominio_de(r["final"])
        # O nome do veículo só é trocado quando não havia um de verdade: a página identifica cada
        # matéria por título e veículo, e trocar o nome perderia o tom já classificado pela jornalista.
        if not reg.get("veiculo") or reg["veiculo"] in ("Sem identificação", reg["dominio"]):
            padronizar(reg)
    if r.get("pub") and r["pub"] != reg.get("data"):
        reg["data_feed"] = reg.get("data")
        reg["data"] = r["pub"]
    if r.get("ini") and not reg.get("resumo"):
        reg["resumo"] = r["ini"]
    if reg.pop("motivo", None):  # resultado de uma verificação anterior: refaz do zero
        reg.pop("descartada", None)
    if situacao == "nao":
        reg["descartada"] = True
        reg["motivo"] = "O texto da matéria não cita a Unifesspa."
    elif situacao == "sim" and PADRAO_INCIDENTAL.search(norm(reg["titulo"])) \
            and not cita(norm(reg["titulo"])):
        reg["descartada"] = True
        reg["motivo"] = "Menção apenas incidental (local de votação ou lista de agenda)."
    return reg

# ---- Utilidades -------------------------------------------------------------
def eh_proprio(item):
    alvo = (item["dominio"] + " " + item["veiculo"]).lower()
    if any(d in alvo for d in DOMINIOS_PERMITIDOS):
        return False
    return any(d in alvo for d in DOMINIOS_IGNORADOS)


def lerdata(txt):
    """Aceita dd/mm/aaaa (padrão brasileiro) ou aaaa-mm-dd."""
    txt = txt.strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", txt):
        d, m, a = map(int, txt.split("/"))
        return date(a, m, d)
    return date.fromisoformat(txt)


def br(d):
    """Data (objeto ou texto aaaa-mm-dd) em dd/mm/aaaa."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.strftime("%d/%m/%Y")


def carregar():
    if JSON_PATH.exists():
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    return []


def salvar(regs):
    PASTA.mkdir(exist_ok=True)
    regs.sort(key=lambda r: (r["data"], r["veiculo"], r["titulo"]), reverse=True)
    JSON_PATH.write_text(json.dumps(regs, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: abre certo no Excel
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data", "Veículo", "Título", "Termos encontrados", "Resumo", "Link"])
        for r in regs:
            if r.get("descartada"):
                continue
            w.writerow([br(r["data"]), r["veiculo"], r["titulo"], ", ".join(r["termos"]),
                        r.get("resumo", ""), r["link"]])


class Indice:
    """Evita duplicar a mesma matéria vinda de fontes diferentes.
    Duas matérias são a mesma se tiverem o mesmo título e o mesmo veículo, ou o mesmo título e o mesmo
    domínio. Registros antigos (sem domínio) também casam por título e data."""

    def __init__(self, regs):
        self.por_veic, self.por_dom, self.por_data, self.por_url = {}, {}, {}, {}
        for r in regs:
            self.add(r)

    def add(self, r):
        t = norm(r["titulo"])
        if r.get("link"):
            self.por_url[limpar_url(r["link"])] = r
        self.por_veic[t + "|" + norm(r["veiculo"])] = r
        if r.get("dominio"):
            self.por_dom[t + "|" + r["dominio"]] = r
        else:
            self.por_data[t + "|" + r["data"]] = r

    def achar(self, it):
        t = norm(it["titulo"])
        return (self.por_url.get(limpar_url(it["link"])) if it.get("link") else None) \
            or (self.por_veic.get(t + "|" + norm(it["veiculo"]))
                or (self.por_dom.get(t + "|" + it["dominio"]) if it["dominio"] else None)
                or self.por_data.get(t + "|" + it["data"]))


def padronizar(it, nome_forcado=None):
    """Dá ao veículo o mesmo nome em todas as fontes."""
    if nome_forcado:
        it["veiculo"] = nome_forcado
    else:
        for dom, nome in NOMES_POR_DOMINIO.items():
            if it["dominio"] == dom or it["dominio"].endswith("." + dom):
                it["veiculo"] = nome
                break
    if not it["veiculo"]:
        it["veiculo"] = it["dominio"] or "Sem identificação"
    return it


def tarefas(fontes, ini, fim, janela, hoje):
    """Gera (fonte, descrição, função que devolve a lista de itens, nome forçado do veículo)."""
    ini_bing = max(ini, hoje - timedelta(days=DIAS_BING))
    ini_gdelt = max(ini, hoje - timedelta(days=DIAS_GDELT))
    if "google" in fontes:
        for termo in TERMOS:
            for i, f in janelas(ini, fim, janela):
                yield ("Google", f"{termo[:26]} {br(i)} a {br(f)}",
                       lambda baixar_fn, avisos, termo=termo, i=i, f=f: coletar_janela(termo, i, f, baixar_fn, avisos), None, termo)
    if "bing" in fontes:
        if ini < ini_bing:
            print(f"Aviso: o Bing Notícias só alcança os últimos {DIAS_BING} dias; consultando de {br(ini_bing)} em diante.")
        if fim >= ini_bing:
            intervalo = 8 if (hoje - ini_bing).days <= 7 else 9
            for termo in TERMOS_BASE:
                yield ("Bing", termo[:40],
                       lambda baixar_fn, avisos, termo=termo, iv=intervalo: consulta_bing(termo, iv, baixar_fn), None, termo)
    if "gdelt" in fontes:
        if ini < ini_gdelt:
            print(f"Aviso: o GDELT só alcança os últimos {DIAS_GDELT} dias; consultando de {br(ini_gdelt)} em diante.")
        if fim >= ini_gdelt:
            for termo in TERMOS_BASE:
                yield ("GDELT", f"{termo[:26]} {br(ini_gdelt)} a {br(fim)}",
                       lambda baixar_fn, avisos, termo=termo: coletar_gdelt(termo, ini_gdelt, fim + timedelta(days=1), baixar_fn, avisos), None, termo)
    if "locais" in fontes:
        for site, nome in LOCAIS_WORDPRESS.items():
            for termo in TERMOS_BASE:
                yield (nome, termo[:40],
                       lambda baixar_fn, avisos, site=site, nome=nome, termo=termo: consulta_local(site, nome, termo, ini, baixar_fn), nome, termo)
    if "sites" in fontes:
        for site, nome in LOCAIS_SEM_FEED.items():
            for termo in TERMOS_SITES:
                for i, f in janelas(ini, fim, max(janela, 14)):
                    yield (nome, f"{termo[:22]} {br(i)} a {br(f)}",
                           lambda baixar_fn, avisos, termo=termo, site=site, i=i, f=f: coletar_janela(termo, i, f, baixar_fn, avisos, site), nome, termo)
                if fim >= ini_bing:
                    iv = 8 if (hoje - ini_bing).days <= 7 else 9
                    yield (nome + " (Bing)", termo[:40],
                           lambda baixar_fn, avisos, termo=termo, site=site, iv=iv: consulta_bing(termo, iv, baixar_fn, site), nome, termo)


def tarefas_extras(fontes, ini, fim, hoje):
    """Fontes que não dependem de busca: o feed geral de cada veículo e o sitemap de notícias."""
    if "feeds" in fontes:
        for site, nome in LOCAIS_WORDPRESS.items():
            yield (nome + " (feed)", "últimas publicadas",
                   lambda baixar_fn, avisos, site=site, nome=nome: consulta_feed(site, nome, ini, baixar_fn),
                   nome, "Feed do veículo")
    if "alertas" in fontes:
        for endereco, termo in ALERTAS_RSS.items():
            yield ("Alerta do Google", termo[:40],
                   lambda baixar_fn, avisos, e=endereco, termo=termo: consulta_alerta(e, termo, baixar_fn),
                   None, termo)
    if "sitemaps" in fontes:
        for endereco, nome in SITEMAPS_NOTICIAS.items():
            yield (nome + " (sitemap)", "últimas 48 horas",
                   lambda baixar_fn, avisos, e=endereco, nome=nome: consulta_sitemap_news(e, nome, baixar_fn),
                   nome, "Sitemap de notícias")


def veiculos_sem_nome(regs):
    """Domínios que já apareceram no clipping e ainda não têm nome próprio na lista acima. Serve para
    batizar os veículos a partir do que realmente aparece, em vez de adivinhar site por site."""
    conhecidos = set(NOMES_POR_DOMINIO)
    achados = {}
    for r in regs:
        if r.get("descartada"):
            continue
        dom = r.get("dominio") or dominio_de(r.get("link", ""))
        if not dom or any(dom == d or dom.endswith("." + d) for d in conhecidos):
            continue
        d = achados.setdefault(dom, {"dominio": dom, "materias": 0, "nomes": [], "exemplo": ""})
        d["materias"] += 1
        if r.get("veiculo") and r["veiculo"] not in d["nomes"]:
            d["nomes"].append(r["veiculo"])
        if not d["exemplo"]:
            d["exemplo"] = r.get("titulo", "")[:80]
    return sorted(achados.values(), key=lambda x: -x["materias"])


def registrar_resumo(numeros):
    """Guarda os números da coleta e, no GitHub, escreve o mesmo resumo na página do Actions,
    para dar para ver num relance se a coleta funcionou."""
    PASTA.mkdir(exist_ok=True)
    RESUMO_PATH.write_text(json.dumps(numeros, ensure_ascii=False, indent=1), encoding="utf-8")
    sem_nome = numeros.pop("sem_nome", [])
    VEICULOS_PATH.write_text(json.dumps(sem_nome, ensure_ascii=False, indent=1), encoding="utf-8")
    caminho = os.environ.get("GITHUB_STEP_SUMMARY")
    if not caminho:
        return
    linhas = [f"### Clipping de {br(numeros['quando'][:10])}", "",
              f"- Matérias novas: **{numeros['novas']}**",
              f"- Descartadas pela verificação do texto: {numeros['descartadas']}",
              f"- Total no clipping: {numeros['total']}",
              f"- Consultas: {numeros['consultas']}, falhas: {numeros['falhas']}", ""]
    if sem_nome:
        linhas += ["", f"**{len(sem_nome)} veículo(s) ainda sem nome próprio.** Os dez mais frequentes:", "",
                   "| Domínio | Matérias | Como aparece hoje |", "| --- | --- | --- |"]
        linhas += [f"| {v['dominio']} | {v['materias']} | {', '.join(v['nomes'][:2])} |" for v in sem_nome[:10]]
        linhas += [""]
    if numeros.get("por_fonte"):
        linhas += ["| Fonte | Novas |", "| --- | --- |"]
        linhas += [f"| {k} | {v} |" for k, v in sorted(numeros["por_fonte"].items(), key=lambda x: -x[1]) if v]
    try:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
    except Exception:
        pass


def principal(argv=None, baixar_fn=baixar):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inicio"); ap.add_argument("--fim")
    ap.add_argument("--dias", type=int, help="coleta só os últimos N dias")
    ap.add_argument("--reverificar", action="store_true",
                    help="não coleta nada novo: lê de novo o texto das matérias já salvas e descarta as que não citam a Unifesspa")
    ap.add_argument("--sem-verificacao", action="store_true", help="não lê o texto das matérias (como antes)")
    ap.add_argument("--janela", type=int, default=7, help="tamanho da janela em dias (padrão 7)")
    ap.add_argument("--fontes", default=",".join(FONTES),
                    help="fontes separadas por vírgula: " + ", ".join(FONTES) + " (padrão: todas)")
    a = ap.parse_args(argv)
    fontes = [f.strip() for f in a.fontes.split(",") if f.strip()]
    invalidas = [f for f in fontes if f not in FONTES]
    if invalidas:
        ap.error("fonte desconhecida: " + ", ".join(invalidas))
    hoje = date.today()
    if a.dias:
        ini, fim = hoje - timedelta(days=a.dias), hoje
    else:
        ini = lerdata(a.inicio) if a.inicio else date(hoje.year, 1, 1)
        fim = lerdata(a.fim) if a.fim else hoje

    regs = carregar()
    carregar_cache()
    if a.reverificar:
        # Entram as que não puderam ser lidas, as descartadas (um descarte merece revisão sempre que a
        # leitura melhora) e as de título de lista eleitoral, cuja regra mudou.
        pendentes = [r for r in regs if not r.get("verificada") or r.get("descartada")
                     or PADRAO_INCIDENTAL.search(norm(r["titulo"]))]
        antes = sum(1 for r in regs if r.get("descartada"))
        for k, r in enumerate(pendentes, 1):
            aplicar_verificacao(r, baixar_fn)
            time.sleep(PAUSA_VERIFICACAO if baixar_fn is baixar else 0)
            if k % 25 == 0:
                print(f"  {k}/{len(pendentes)} verificadas", flush=True)
        salvar(regs); salvar_cache()
        PASTA.mkdir(exist_ok=True)
        VEICULOS_PATH.write_text(json.dumps(veiculos_sem_nome(regs), ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        depois = sum(1 for r in regs if r.get("descartada"))
        nao_lidas = sum(1 for r in pendentes if not r["verificada"])
        print(f"Reverificação: {len(pendentes)} matérias revistas, saldo de {depois - antes} descartadas "
              f"({depois} no total), {nao_lidas} não puderam ser lidas (mantidas). Clipping: {len(regs)}.")
        return 0
    indice = Indice(regs)
    novos, avisos, falhas, total_consultas, descartadas = 0, [], 0, 0, 0
    por_fonte = {}
    todas_tarefas = list(tarefas(fontes, ini, fim, a.janela, hoje)) + list(tarefas_extras(fontes, ini, fim, hoje))
    for fonte, desc, fn, nome, termo in todas_tarefas:
        total_consultas += 1
        try:
            itens = fn(baixar_fn, avisos)
        except Exception as e:
            falhas += 1
            print(f"  falha em {fonte} '{desc}': {e}", file=sys.stderr)
            continue
        achou = 0
        for it in itens:
            if not it["titulo"] or not it["data"]:
                continue
            if not (ini.isoformat() <= it["data"] <= fim.isoformat()):
                continue
            it = padronizar(it, nome)
            if eh_proprio(it) or not eh_materia(it["link"]):
                continue
            existente = indice.achar(it)
            if existente:
                if it["dominio"] and not existente.get("dominio"):
                    existente["dominio"] = it["dominio"]
                fs = existente.setdefault("fontes", [])
                if fonte not in fs:
                    fs.append(fonte)
                if termo not in existente["termos"]:
                    existente["termos"].append(termo)
                if it.get("resumo") and not existente.get("resumo"):
                    existente["resumo"] = it["resumo"]
                continue
            reg = {"data": it["data"], "veiculo": it["veiculo"], "titulo": it["titulo"],
                   "link": it["link"], "termos": [termo], "resumo": it.get("resumo", ""),
                   "dominio": it["dominio"], "fontes": [fonte]}
            if VERIFICAR_TEXTO and not a.sem_verificacao:
                aplicar_verificacao(reg, baixar_fn)
                time.sleep(PAUSA_VERIFICACAO if baixar_fn is baixar else 0)
                if reg.get("descartada"):
                    descartadas += 1
            indice.add(reg); regs.append(reg); novos += 1; achou += 1
        por_fonte[fonte] = por_fonte.get(fonte, 0) + achou
        print(f"{fonte:18} {desc[:60]:60} {len(itens):4} itens, {achou} novos", flush=True)
    salvar(regs); salvar_cache()
    registrar_resumo({"quando": datetime.now().isoformat(timespec="seconds"), "novas": novos,
                      "descartadas": descartadas, "total": len(regs), "consultas": total_consultas,
                      "falhas": falhas, "por_fonte": por_fonte, "sem_nome": veiculos_sem_nome(regs)})
    print("\nNovas matérias por fonte:", ", ".join(f"{k} {v}" for k, v in por_fonte.items()) or "nenhuma")
    print(f"Descartadas pela verificação do texto: {descartadas}")
    print(f"Concluído: {novos} matérias novas, {len(regs)} no total. Consultas: {total_consultas}. Falhas: {falhas}.")
    for av in avisos:
        print("Aviso:", av)
    # Falhar de verdade quando a maior parte das consultas der erro: sinal verde com coleta quebrada
    # é pior que vermelho, porque ninguém percebe.
    if falhas and (not regs or falhas >= max(1, total_consultas // 2)):
        print("Erro: metade ou mais das consultas falhou.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(principal())
