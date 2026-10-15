#!/usr/bin/env python3
"""Coletor de clipping da Unifesspa (várias fontes, sem chave de acesso e sem custo).

Fontes:
  google  Google Notícias (busca por termo, em janelas de dias)
  bing    Bing Notícias (só devolve o último mês; serve para a rotina diária)
  gdelt   GDELT (base aberta de notícias; cobre cerca de 90 dias)
  locais  Busca no próprio site de veículos locais que usam WordPress (todo o período)
  sites   Google Notícias restrito a sites sem feed (DOL Carajás, Gazeta Carajás, G1 Pará)

Uso:
  python coletar.py --inicio 2026-01-01 --fim 2026-10-05      # histórico por período
  python coletar.py --dias 3                                  # rotina diária (últimos 3 dias)
  python coletar.py --inicio 2026-09-01 --fim 2026-09-30      # teste de um mês
  python coletar.py --dias 30 --fontes bing,gdelt             # só algumas fontes

Os resultados ficam em dados/clipping.json e dados/clipping.csv. Rodar de novo
acrescenta o que for novo e não duplica o que já existe.
"""
import argparse, base64, csv, html, json, re, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
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
FONTES = ["google", "bing", "gdelt", "locais", "sites"]
# Verificação do texto: a busca (principalmente a do Google, que aceita variações como "Unifesp") traz
# matérias que não falam da universidade. Antes de entrar no clipping, o texto da matéria é lido e
# conferido. O que não cita a Unifesspa fica marcado como "descartada" (não some: dá para ver e restaurar
# na página, marcando "Mostrar matérias descartadas").
VERIFICAR_TEXTO = True
PAUSA_VERIFICACAO = 1.0
# Aparece no texto da matéria (comparado sem acento e sem maiúsculas).
PADRAO_MENCAO = re.compile(
    r"\bunifesspa\b|universidade federal do sul e sudeste do para|\bfrancisco ribeiro da costa\b"
    r"|\blucelia cardoso cavalcante\b|\blucelia cavalcante\b")
# Títulos de matérias que só listam locais de votação etc.: a Unifesspa aparece apenas como endereço.
PADRAO_INCIDENTAL = re.compile(
    r"zona eleitoral|locais? de votacao|resultado das eleicoes|apuracao|boca de urna|"
    r"confira a agenda dos presidenciaveis")
# ----------------------------------------------------------------------------

PASTA = Path(__file__).parent / "dados"
JSON_PATH, CSV_PATH = PASTA / "clipping.json", PASTA / "clipping.csv"
UA = "Mozilla/5.0 (compatible; ClippingASCOM/1.0)"


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


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


def _item(titulo, veiculo, dominio, data, link):
    dom = dominio_de(dominio) if dominio else ""
    return {"titulo": (titulo or "").strip(), "veiculo": (veiculo or "").strip(),
            "dominio": dom, "data": data, "link": (link or "").strip()}


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
                           data_rfc(it.findtext("pubDate")), real))
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
    itens = []
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
                                data_rfc(it.findtext("pubDate")), it.findtext("link")))
        if not pagina:
            break
        itens += pagina
        datas = [x["data"] for x in pagina if x["data"]]
        if datas and min(datas) < ini.isoformat():
            break
        time.sleep(PAUSA if baixar_fn is baixar else 0)
    return itens


# ---- Verificação do texto da matéria ----------------------------------------
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

def texto_da_pagina(url, baixar_fn=baixar):
    """Texto principal de uma página (sem menus, rodapé, scripts), já sem acento e em minúsculas."""
    bruto = baixar_fn(url)
    try:
        pag = bruto.decode("utf-8")
    except UnicodeDecodeError:
        pag = bruto.decode("latin-1")
    pag = re.sub(r"(?is)<(script|style|noscript|svg|nav|header|footer|aside|form)\b.*?</\1>", " ", pag)
    pag = re.sub(r"(?is)<!--.*?-->", " ", pag)
    m = re.search(r"(?is)<article\b.*?</article>", pag) or re.search(r"(?is)<main\b.*?</main>", pag)
    if m:
        pag = m.group(0)
    return norm(html.unescape(re.sub(r"(?s)<[^>]+>", " ", pag)))

def verificar(it, baixar_fn=baixar):
    """Confere se a matéria cita a Unifesspa. Devolve (situação, nº de citações):
    'sim', 'nao' (leu o texto e não achou) ou '?' (não foi possível ler a matéria)."""
    n_titulo = len(PADRAO_MENCAO.findall(norm(it["titulo"])))
    if n_titulo:
        return "sim", n_titulo
    link = it["link"]
    if "news.google.com" in dominio_de(link):
        link = endereco_google(link, baixar_fn)
    if not link:
        return "?", 0
    try:
        texto = texto_da_pagina(link, baixar_fn)
    except Exception:
        return "?", 0
    if len(texto) < 300:  # página vazia, bloqueio ou aviso de cookies: não dá para concluir
        return "?", 0
    n = len(PADRAO_MENCAO.findall(texto))
    return ("sim" if n else "nao"), n

def aplicar_verificacao(reg, baixar_fn=baixar):
    """Grava no registro o resultado da verificação. Só descarta quando leu a matéria e a Unifesspa não aparece,
    ou quando o título indica lista de locais de votação e a universidade aparece só uma vez."""
    situacao, n = verificar(reg, baixar_fn)
    reg["verificada"] = situacao != "?"
    reg["mencoes"] = n
    if reg.pop("motivo", None):  # resultado de uma verificação anterior: refaz do zero
        reg.pop("descartada", None)
    if situacao == "nao":
        reg["descartada"] = True
        reg["motivo"] = "O texto da matéria não cita a Unifesspa."
    elif situacao == "sim" and n <= 1 and PADRAO_INCIDENTAL.search(norm(reg["titulo"])) \
            and not PADRAO_MENCAO.search(norm(reg["titulo"])):
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
        w.writerow(["Data", "Veículo", "Título", "Termos encontrados", "Link"])
        for r in regs:
            if r.get("descartada"):
                continue
            w.writerow([br(r["data"]), r["veiculo"], r["titulo"], ", ".join(r["termos"]), r["link"]])


class Indice:
    """Evita duplicar a mesma matéria vinda de fontes diferentes.
    Duas matérias são a mesma se tiverem o mesmo título e o mesmo veículo, ou o mesmo título e o mesmo
    domínio. Registros antigos (sem domínio) também casam por título e data."""

    def __init__(self, regs):
        self.por_veic, self.por_dom, self.por_data = {}, {}, {}
        for r in regs:
            self.add(r)

    def add(self, r):
        t = norm(r["titulo"])
        self.por_veic[t + "|" + norm(r["veiculo"])] = r
        if r.get("dominio"):
            self.por_dom[t + "|" + r["dominio"]] = r
        else:
            self.por_data[t + "|" + r["data"]] = r

    def achar(self, it):
        t = norm(it["titulo"])
        return (self.por_veic.get(t + "|" + norm(it["veiculo"]))
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
                       lambda baixar_fn, avisos, site=site, nome=nome, termo=termo: consulta_wordpress(site, nome, termo, ini, baixar_fn), nome, termo)
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
    if a.reverificar:
        pendentes = [r for r in regs if "verificada" not in r or not r["verificada"]]
        antes = sum(1 for r in regs if r.get("descartada"))
        for k, r in enumerate(pendentes, 1):
            aplicar_verificacao(r, baixar_fn)
            time.sleep(PAUSA_VERIFICACAO if baixar_fn is baixar else 0)
            if k % 25 == 0:
                print(f"  {k}/{len(pendentes)} verificadas", flush=True)
        salvar(regs)
        depois = sum(1 for r in regs if r.get("descartada"))
        nao_lidas = sum(1 for r in pendentes if not r["verificada"])
        print(f"Reverificação: {len(pendentes)} matérias lidas, {depois - antes} novas descartadas, "
              f"{nao_lidas} não puderam ser lidas (mantidas). Total: {len(regs)}.")
        return 0
    indice = Indice(regs)
    novos, avisos, falhas, total_consultas, descartadas = 0, [], 0, 0, 0
    por_fonte = {}
    for fonte, desc, fn, nome, termo in tarefas(fontes, ini, fim, a.janela, hoje):
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
            if eh_proprio(it):
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
                continue
            reg = {"data": it["data"], "veiculo": it["veiculo"], "titulo": it["titulo"],
                   "link": it["link"], "termos": [termo],
                   "dominio": it["dominio"], "fontes": [fonte]}
            if VERIFICAR_TEXTO and not a.sem_verificacao:
                aplicar_verificacao(reg, baixar_fn)
                time.sleep(PAUSA_VERIFICACAO if baixar_fn is baixar else 0)
                if reg.get("descartada"):
                    descartadas += 1
            indice.add(reg); regs.append(reg); novos += 1; achou += 1
        por_fonte[fonte] = por_fonte.get(fonte, 0) + achou
        print(f"{fonte:18} {desc[:60]:60} {len(itens):4} itens, {achou} novos", flush=True)
    salvar(regs)
    print("\nNovas matérias por fonte:", ", ".join(f"{k} {v}" for k, v in por_fonte.items()) or "nenhuma")
    print(f"Descartadas pela verificação do texto: {descartadas}")
    print(f"Concluído: {novos} matérias novas, {len(regs)} no total. Consultas: {total_consultas}. Falhas: {falhas}.")
    for av in avisos:
        print("Aviso:", av)
    return 1 if falhas and not regs else 0


if __name__ == "__main__":
    sys.exit(principal())
