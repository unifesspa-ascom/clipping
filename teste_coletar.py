#!/usr/bin/env python3
"""Testes do coletor. Não acessam a internet: todas as páginas são simuladas.

Rodar:  python teste_coletar.py
O trecho mais frágil é o que descobre o endereço real dos links do Google Notícias; se o Google mudar
o formato, é aqui que se percebe, e não com o clipping já cheio de matérias erradas.
"""
import base64, json, shutil, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

import coletar as c

HOJE = date.today().isoformat()
ENCHE = "Este é o corpo da matéria com texto suficiente para a verificação levar a sério. " * 8


def pagina(corpo, data=None):
    meta = f'<meta property="article:published_time" content="{data}T10:00:00-03:00">' if data else ""
    return (f"<html><head>{meta}</head><body><nav>Unifesspa menu</nav>"
            f"<article>{corpo} {ENCHE}</article><footer>Unifesspa rodapé</footer></body></html>")


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        (self.pasta / "dados").mkdir()
        c.PASTA = self.pasta / "dados"
        c.JSON_PATH = c.PASTA / "clipping.json"
        c.CSV_PATH = c.PASTA / "clipping.csv"
        c.CACHE_PATH = c.PASTA / "cache_textos.json"
        c.RESUMO_PATH = c.PASTA / "ultima_coleta.json"
        c.CACHE_TEXTOS = {}
        self.baixados = []
        self.paginas = {}

    def tearDown(self):
        shutil.rmtree(self.pasta, ignore_errors=True)

    def baixar(self, url):
        self.baixados.append(url)
        for chave, valor in self.paginas.items():
            if chave in url:
                return valor if isinstance(valor, bytes) else valor.encode("utf-8")
        raise RuntimeError("sem resposta para " + url)


class TestUtilidades(Base):
    def test_norm_tira_acento_e_maiuscula(self):
        self.assertEqual(c.norm("Pará, Sudeste!"), "para sudeste")

    def test_limpar_url_tira_rastreador_e_barra(self):
        self.assertEqual(c.limpar_url("https://X.com/noticia/?utm_source=fb&id=3"),
                         "https://x.com/noticia?id=3")

    def test_limpar_resumo_corta_e_tira_marcacao(self):
        r = c.limpar_resumo("<p>Olá <b>mundo</b></p> " + "palavra " * 100)
        self.assertTrue(r.startswith("Olá mundo"))
        self.assertLessEqual(len(r), 244)

    def test_dominio_sem_www(self):
        self.assertEqual(c.dominio_de("https://www.Correio.com.br/a"), "correio.com.br")


class TestLeitura(Base):
    def test_texto_ignora_menu_e_rodape(self):
        texto = c.texto_do_html(pagina("Corpo da notícia."))
        self.assertIn("Corpo da notícia", texto)
        self.assertNotIn("menu", texto)
        self.assertNotIn("rodapé", texto)

    def test_data_declarada_pelo_site(self):
        self.assertEqual(c.data_do_html(pagina("x", "2026-03-04")), "2026-03-04")
        self.assertEqual(c.data_do_html('<html>"datePublished":"2026-05-06T08:00:00Z"</html>'), "2026-05-06")
        self.assertEqual(c.data_do_html("<html>sem data</html>"), "")

    def test_data_absurda_e_ignorada(self):
        futuro = (date.today() + timedelta(days=400)).isoformat()
        self.assertEqual(c.data_do_html(pagina("x", futuro)), "")
        self.assertEqual(c.data_do_html(pagina("x", "1998-01-01")), "")

    def test_endereco_google_formato_antigo(self):
        bruto = b"\x08\x13\x22\x20https://veiculo.com/materia\xd2\x01\x00"
        id_ = base64.urlsafe_b64encode(bruto).decode().rstrip("=")
        achado = c.endereco_google(f"https://news.google.com/rss/articles/{id_}?oc=5", self.baixar)
        self.assertEqual(achado, "https://veiculo.com/materia")

    def test_le_a_materia_uma_vez_so(self):
        self.paginas["veiculo.com/a"] = pagina("A Unifesspa abriu vagas.", "2026-02-01")
        r1 = c.ler_artigo("https://veiculo.com/a", self.baixar)
        r2 = c.ler_artigo("https://veiculo.com/a", self.baixar)
        self.assertEqual(r1, r2)
        self.assertEqual(len(self.baixados), 1, "a segunda leitura tem de vir do cache")
        self.assertEqual(r1["m"], 1)
        self.assertEqual(r1["pub"], "2026-02-01")
        self.assertTrue(r1["ini"].startswith("A Unifesspa abriu vagas"))

    def test_pagina_curta_nao_conclui_nada(self):
        self.paginas["veiculo.com/b"] = "<html>aceite os cookies</html>"
        self.assertFalse(c.ler_artigo("https://veiculo.com/b", self.baixar)["ok"])


def materia(titulo, link, data=HOJE):
    return {"data": data, "veiculo": "Veículo", "titulo": titulo, "link": link,
            "termos": ["Unifesspa"], "dominio": c.dominio_de(link), "fontes": ["Google"], "resumo": ""}


class TestVerificacao(Base):
    def test_cita_no_texto_fica(self):
        self.paginas["v.com/a"] = pagina("A Unifesspa abriu vagas.")
        r = c.aplicar_verificacao(materia("Novidade em Marabá", "https://v.com/a"), self.baixar)
        self.assertNotIn("descartada", r)
        self.assertTrue(r["verificada"])

    def test_nao_cita_e_descartada(self):
        self.paginas["v.com/b"] = pagina("Vestibular Unifesp 2027 abre inscrições.")
        r = c.aplicar_verificacao(materia("Vestibular Unifesp 2027", "https://v.com/b"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("não cita", r["motivo"])

    def test_pagina_inacessivel_e_mantida(self):
        r = c.aplicar_verificacao(materia("Qualquer", "https://v.com/sem-resposta"), self.baixar)
        self.assertNotIn("descartada", r)
        self.assertFalse(r["verificada"])

    def test_local_de_votacao_e_descartado(self):
        self.paginas["v.com/c"] = pagina("Seção na Universidade Federal do Sul e Sudeste do Pará, 51ª zona.")
        r = c.aplicar_verificacao(
            materia("Resultado das eleições 2026 em Rondon do Pará", "https://v.com/c"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("incidental", r["motivo"])

    def test_restaura_quando_passa_a_citar(self):
        self.paginas["v.com/d"] = pagina("A Unifesspa anunciou o resultado.")
        reg = materia("Notícia", "https://v.com/d")
        reg["descartada"] = True
        reg["motivo"] = "O texto da matéria não cita a Unifesspa."
        self.assertNotIn("descartada", c.aplicar_verificacao(reg, self.baixar))

    def test_nomeia_veiculo_quando_so_havia_o_dominio(self):
        self.paginas["v.com/e"] = pagina("A Unifesspa participou do evento.")
        reg = materia("Notícia", "https://v.com/e")
        reg["veiculo"] = "v.com"
        reg["dominio"] = "v.com"
        c.NOMES_POR_DOMINIO["v.com"] = "Veículo Batizado"
        try:
            c.aplicar_verificacao(reg, self.baixar)
        finally:
            c.NOMES_POR_DOMINIO.pop("v.com")
        self.assertEqual(reg["veiculo"], "Veículo Batizado")

    def test_guarda_link_real_data_e_resumo(self):
        bruto = b"\x08\x13\x22\x20https://correiodecarajas.com.br/x\xd2\x01\x00"
        id_ = base64.urlsafe_b64encode(bruto).decode().rstrip("=")
        self.paginas["correiodecarajas.com.br/x"] = pagina("A Unifesspa inaugurou o prédio.", "2026-04-02")
        reg = materia("Prédio novo", f"https://news.google.com/rss/articles/{id_}", data="2026-04-09")
        c.aplicar_verificacao(reg, self.baixar)
        self.assertEqual(reg["link"], "https://correiodecarajas.com.br/x")
        self.assertEqual(reg["veiculo"], "Veículo", "o nome já existente é preservado")
        self.assertEqual(reg["data"], "2026-04-02", "a data do site corrige a do agregador")
        self.assertEqual(reg["data_feed"], "2026-04-09")
        self.assertTrue(reg["resumo"])


class TestFontes(Base):
    def test_google_noticias(self):
        self.paginas["news.google.com/rss/search"] = """<rss><channel><item>
            <title>Unifesspa abre vagas - Correio de Carajás</title>
            <link>https://news.google.com/rss/articles/ABC</link>
            <pubDate>Tue, 06 Oct 2026 12:00:00 GMT</pubDate>
            <source url="https://correiodecarajas.com.br">Correio de Carajás</source>
            </item></channel></rss>"""
        itens = c.consulta("Unifesspa", date(2026, 10, 1), date(2026, 10, 8), self.baixar)
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["titulo"], "Unifesspa abre vagas")
        self.assertEqual(itens[0]["data"], "2026-10-06")
        self.assertEqual(itens[0]["dominio"], "correiodecarajas.com.br")

    def test_api_do_wordpress(self):
        self.paginas["wp-json/wp/v2/posts"] = json.dumps([
            {"link": "https://v.com/1", "title": {"rendered": "Unifesspa &amp; Vale"},
             "date": "2026-09-30T09:00:00", "excerpt": {"rendered": "<p>Trecho da matéria.</p>"}}])
        itens = c.consulta_wp_rest("https://v.com", "Veículo", "Unifesspa", date(2026, 9, 1), self.baixar)
        self.assertEqual(itens[0]["titulo"], "Unifesspa & Vale")
        self.assertEqual(itens[0]["data"], "2026-09-30")
        self.assertEqual(itens[0]["resumo"], "Trecho da matéria.")

    def test_busca_local_usa_feed_quando_nao_ha_api(self):
        self.paginas["feed=rss2"] = """<rss><channel><item><title>Unifesspa em Canaã</title>
            <link>https://v.com/2</link><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate>
            <description>Resumo</description></item></channel></rss>"""
        itens = c.consulta_local("https://v.com", "Veículo", "Unifesspa", date(2026, 9, 1), self.baixar)
        self.assertEqual(len(itens), 1)
        self.assertTrue(any("wp-json" in u for u in self.baixados), "tenta a API antes do feed")

    def test_feed_geral_filtra_pelo_texto(self):
        self.paginas["/feed/"] = """<rss><channel>
            <item><title>Obra em Marabá</title><link>https://v.com/a</link>
              <pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate><description>Sem pista no resumo</description></item>
            <item><title>Congresso na Unifesspa</title><link>https://v.com/b</link>
              <pubDate>Mon, 28 Sep 2026 11:00:00 GMT</pubDate><description>x</description></item>
            <item><title>Trânsito parado</title><link>https://v.com/c</link>
              <pubDate>Mon, 28 Sep 2026 12:00:00 GMT</pubDate><description>y</description></item>
            </channel></rss>"""
        self.paginas["v.com/a"] = pagina("Obra entregue pela Unifesspa.")
        self.paginas["v.com/c"] = pagina("Nada a ver com a universidade.")
        itens = c.consulta_feed("https://v.com", "Veículo", date(2026, 9, 1), self.baixar)
        self.assertEqual(sorted(x["titulo"] for x in itens), ["Congresso na Unifesspa", "Obra em Marabá"])

    def test_feed_respeita_o_limite_de_leituras(self):
        itens = [c._item(f"Matéria {i}", "V", f"https://v.com/{i}", HOJE, f"https://v.com/{i}")
                 for i in range(10)]
        for i in range(10):
            self.paginas[f"v.com/{i}"] = pagina("Fala da Unifesspa.")
        guardados = c.filtrar_mencao(itens, self.baixar, limite=3)
        self.assertEqual(len(guardados), 3)
        self.assertEqual(len(self.baixados), 3)

    def test_sitemap_de_noticias(self):
        self.paginas["sitemap-news.xml"] = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
            <url><loc>https://g.com/1</loc><news:news><news:title>Unifesspa firma parceria</news:title>
              <news:publication_date>2026-10-14T08:00:00-03:00</news:publication_date></news:news></url>
            <url><loc>https://g.com/2</loc><news:news><news:title>Chuva alaga avenida</news:title>
              <news:publication_date>2026-10-14T09:00:00-03:00</news:publication_date></news:news></url>
            </urlset>"""
        self.paginas["g.com/2"] = pagina("Sem relação com a universidade.")
        itens = c.consulta_sitemap_news("https://g.com/sitemap-news.xml", "Gazeta Carajás", self.baixar)
        self.assertEqual([x["titulo"] for x in itens], ["Unifesspa firma parceria"])
        self.assertEqual(itens[0]["data"], "2026-10-14")


class TestArquivo(Base):
    def test_nao_duplica_pelo_endereco(self):
        indice = c.Indice([materia("Título de um jeito", "https://v.com/a/?utm_source=x")])
        self.assertIsNotNone(indice.achar(materia("Título de outro jeito", "https://v.com/a")))

    def test_nao_duplica_pelo_titulo_e_veiculo(self):
        indice = c.Indice([materia("Mesma matéria", "https://v.com/a")])
        self.assertIsNotNone(indice.achar(materia("Mesma  matéria!", "https://outro.com/b")))

    def test_planilha_nao_leva_as_descartadas(self):
        boa = materia("Fica", "https://v.com/a")
        ruim = materia("Sai", "https://v.com/b")
        ruim["descartada"] = True
        c.salvar([boa, ruim])
        planilha = c.CSV_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("Fica", planilha)
        self.assertNotIn("Sai", planilha)


class TestRotina(Base):
    def test_reverificacao_limpa_o_que_ja_estava_salvo(self):
        self.paginas["v.com/a"] = pagina("A Unifesspa divulgou o edital.")
        self.paginas["v.com/b"] = pagina("Vestibular Unifesp 2027.")
        c.JSON_PATH.write_text(json.dumps([materia("Edital", "https://v.com/a"),
                                           materia("Vestibular Unifesp", "https://v.com/b")]),
                               encoding="utf-8")
        self.assertEqual(c.principal(["--reverificar"], self.baixar), 0)
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertTrue(salvos["Vestibular Unifesp"]["descartada"])
        self.assertNotIn("descartada", salvos["Edital"])
        self.assertTrue(c.CACHE_PATH.exists(), "o cache evita reler tudo na próxima vez")

    def test_coleta_diaria_de_ponta_a_ponta(self):
        termos = c.TERMOS
        c.TERMOS = ["Unifesspa"]
        try:
            self.paginas["news.google.com/rss/search"] = f"""<rss><channel>
                <item><title>Unifesspa lança curso - Correio de Carajás</title>
                  <link>https://correiodecarajas.com.br/novo</link>
                  <pubDate>{date.today().strftime('%a, %d %b %Y')} 12:00:00 GMT</pubDate>
                  <source url="https://correiodecarajas.com.br">Correio de Carajás</source></item>
                <item><title>Vestibular Unifesp 2027 - Blog do Vestibular</title>
                  <link>https://blog.com/unifesp</link>
                  <pubDate>{date.today().strftime('%a, %d %b %Y')} 12:00:00 GMT</pubDate>
                  <source url="https://blog.com">Blog do Vestibular</source></item>
                </channel></rss>"""
            self.paginas["correiodecarajas.com.br/novo"] = pagina("A Unifesspa lançou o curso.")
            self.paginas["blog.com/unifesp"] = pagina("A Unifesp, de São Paulo, abriu inscrições.")
            self.assertEqual(c.principal(["--dias", "1", "--fontes", "google"], self.baixar), 0)
        finally:
            c.TERMOS = termos
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertNotIn("descartada", salvos["Unifesspa lança curso"])
        self.assertTrue(salvos["Vestibular Unifesp 2027"]["descartada"])
        numeros = json.loads(c.RESUMO_PATH.read_text(encoding="utf-8"))
        self.assertEqual(numeros["novas"], 2)
        self.assertEqual(numeros["descartadas"], 1)

    def test_falha_quando_quase_tudo_da_erro(self):
        termos = c.TERMOS
        c.TERMOS = ["Unifesspa"]
        try:
            self.assertEqual(c.principal(["--dias", "1", "--fontes", "google"], self.baixar), 1)
        finally:
            c.TERMOS = termos


if __name__ == "__main__":
    unittest.main(verbosity=2)
