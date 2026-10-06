#!/usr/bin/env python3
"""Coletor de clipping da Unifesspa (Google Notícias, sem chave de acesso e sem custo).

Uso:
  python coletar.py --inicio 2026-01-01 --fim 2026-10-05      # histórico por período
  python coletar.py --dias 3                                  # rotina diária (últimos 3 dias)
  python coletar.py --inicio 2026-09-01 --fim 2026-09-30      # teste de um mês

Os resultados ficam em dados/clipping.json e dados/clipping.csv. Rodar de novo
acrescenta o que for novo e não duplica o que já existe.
"""
import argparse, csv, json, re, sys, time, unicodedata, urllib.parse, urllib.request
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
# Veículos próprios da universidade: não entram no clipping (é o que a imprensa publica sobre ela).
DOMINIOS_IGNORADOS = ["unifesspa.edu.br"]
LIMITE_FEED = 100        # o Google devolve no máximo ~100 itens por consulta
PAUSA = 1.5              # segundos entre consultas, para não sobrecarregar o serviço
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


def consulta(termo, ini, fim, baixar_fn=baixar):
    """Itens do feed para um termo entre ini (inclusive) e fim (exclusive)."""
    base = termo if '"' in termo else f'"{termo}"'   # termo com aspas próprias é usado como foi escrito
    q = f'{base} after:{ini.isoformat()} before:{fim.isoformat()}'
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
        try:
            data = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:
            data = ""
        itens.append({"titulo": titulo, "veiculo": veiculo, "dominio": dominio,
                      "data": data, "link": (it.findtext("link") or "").strip()})
    return itens


def janelas(ini, fim, passo):
    d = ini
    while d <= fim:
        yield d, min(d + timedelta(days=passo), fim + timedelta(days=1))
        d += timedelta(days=passo)


def coletar_janela(termo, ini, fim, baixar_fn, avisos):
    itens = consulta(termo, ini, fim, baixar_fn)
    time.sleep(PAUSA if baixar_fn is baixar else 0)
    if len(itens) >= LIMITE_FEED and (fim - ini).days > 1:
        meio = ini + (fim - ini) / 2
        meio = ini + timedelta(days=(fim - ini).days // 2)
        return (coletar_janela(termo, ini, meio, baixar_fn, avisos)
                + coletar_janela(termo, meio, fim, baixar_fn, avisos))
    if len(itens) >= LIMITE_FEED:
        avisos.append(f"{termo} em {ini}: pode haver mais itens que o limite do feed")
    return itens


def eh_proprio(item):
    alvo = (item["dominio"] + " " + item["veiculo"]).lower()
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
            w.writerow([br(r["data"]), r["veiculo"], r["titulo"], ", ".join(r["termos"]), r["link"]])


def principal(argv=None, baixar_fn=baixar):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inicio"); ap.add_argument("--fim")
    ap.add_argument("--dias", type=int, help="coleta só os últimos N dias")
    ap.add_argument("--janela", type=int, default=7, help="tamanho da janela em dias (padrão 7)")
    a = ap.parse_args(argv)
    hoje = date.today()
    if a.dias:
        ini, fim = hoje - timedelta(days=a.dias), hoje
    else:
        ini = lerdata(a.inicio) if a.inicio else date(hoje.year, 1, 1)
        fim = lerdata(a.fim) if a.fim else hoje

    regs = carregar()
    chave = {norm(r["titulo"]) + "|" + norm(r["veiculo"]): r for r in regs}
    novos, avisos, falhas = 0, [], 0
    for termo in TERMOS:
        for i, f in janelas(ini, fim, a.janela):
            try:
                itens = coletar_janela(termo, i, f, baixar_fn, avisos)
            except Exception as e:
                falhas += 1
                print(f"  falha em '{termo}' {br(i)}: {e}", file=sys.stderr)
                continue
            for it in itens:
                if eh_proprio(it) or not it["titulo"]:
                    continue
                k = norm(it["titulo"]) + "|" + norm(it["veiculo"])
                if k in chave:
                    if termo not in chave[k]["termos"]:
                        chave[k]["termos"].append(termo)
                    continue
                reg = {"data": it["data"], "veiculo": it["veiculo"], "titulo": it["titulo"],
                       "link": it["link"], "termos": [termo]}
                chave[k] = reg; regs.append(reg); novos += 1
            print(f"{termo[:28]:28} {br(i)} a {br(f)}: {len(itens)} itens", flush=True)
    salvar(regs)
    print(f"\nConcluído: {novos} matérias novas, {len(regs)} no total. Falhas: {falhas}.")
    for av in avisos:
        print("Aviso:", av)
    return 1 if falhas and not regs else 0


if __name__ == "__main__":
    sys.exit(principal())
