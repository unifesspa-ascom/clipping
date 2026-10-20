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


def pagina(corpo, data=None, titulo="Matéria de teste do clipping"):
    meta = f'<meta property="article:published_time" content="{data}T10:00:00-03:00">' if data else ""
    return (f"<html><head>{meta}</head><body><nav>Unifesspa menu</nav>"
            f"<article><h1>{titulo}</h1>{corpo} {ENCHE}</article>"
            f"<footer>Unifesspa rodapé</footer></body></html>")


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        (self.pasta / "dados").mkdir()
        c.PASTA = self.pasta / "dados"
        c.JSON_PATH = c.PASTA / "clipping.json"
        c.CSV_PATH = c.PASTA / "clipping.csv"
        c.CACHE_PATH = c.PASTA / "cache_textos.json"
        c.RESUMO_PATH = c.PASTA / "ultima_coleta.json"
        c.VEICULOS_PATH = c.PASTA / "veiculos_sem_nome.json"
        c.REVISOES_PATH = c.PASTA / "revisoes.json"
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
        r1 = c.ler_artigo("https://veiculo.com/a", "Matéria de teste do clipping", self.baixar)
        r2 = c.ler_artigo("https://veiculo.com/a", "Matéria de teste do clipping", self.baixar)
        self.assertEqual(r1, r2)
        self.assertEqual(len(self.baixados), 1, "a segunda leitura tem de vir do cache")
        self.assertEqual(r1["m"], 1)
        self.assertEqual(r1["pub"], "2026-02-01")
        self.assertIn("A Unifesspa abriu vagas", r1["ini"])

    def test_mencao_so_no_menu_nao_conta_mas_nao_descarta(self):
        self.paginas["v.com/menu"] = ("<html><body><header>Portal Unifesspa Notícias</header>"
                                      "<article><h1>Chuva alaga avenida</h1>A chuva alagou a avenida. "
                                      + ENCHE + "</article></body></html>")
        r = c.ler_artigo("https://v.com/menu", "Chuva alaga avenida", self.baixar)
        self.assertTrue(r["ok"])
        self.assertEqual(r["m"], 0)
        self.assertTrue(r["margem"])
        reg = materia("Chuva alaga avenida", "https://v.com/menu")
        c.aplicar_verificacao(reg, self.baixar)
        self.assertNotIn("descartada", reg, "citação só no cabeçalho não autoriza descartar")
        self.assertFalse(reg["verificada"])

    def test_pagina_compactada_e_descompactada(self):
        import gzip as g
        self.paginas["v.com/gz"] = g.compress(pagina("A Unifesspa assinou o convênio.",
                                                     titulo="Convênio assinado").encode("utf-8"))
        r = c.ler_artigo("https://v.com/gz", "Convênio assinado", lambda u: self.baixar(u))
        self.assertTrue(r["ok"])
        self.assertEqual(r["m"], 1)

    def test_pagina_curta_nao_conclui_nada(self):
        self.paginas["veiculo.com/b"] = "<html>aceite os cookies</html>"
        self.assertFalse(c.ler_artigo("https://veiculo.com/b", "Qualquer", self.baixar)["ok"])


def materia(titulo, link, data=HOJE):
    return {"data": data, "veiculo": "Veículo", "titulo": titulo, "link": link,
            "termos": ["Unifesspa"], "dominio": c.dominio_de(link), "fontes": ["Google"], "resumo": ""}


class TestVerificacao(Base):
    def test_cita_no_texto_fica(self):
        self.paginas["v.com/a"] = pagina("A Unifesspa abriu vagas.", titulo="Novidade em Marabá")
        r = c.aplicar_verificacao(materia("Novidade em Marabá", "https://v.com/a"), self.baixar)
        self.assertNotIn("descartada", r)
        self.assertTrue(r["verificada"])

    def test_nao_cita_e_descartada(self):
        self.paginas["v.com/b"] = pagina("Vestibular Unifesp 2027 abre inscrições.", titulo="Vestibular Unifesp 2027")
        r = c.aplicar_verificacao(materia("Vestibular Unifesp 2027", "https://v.com/b"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("não cita", r["motivo"])

    def test_pagina_inacessivel_sem_pista_e_descartada(self):
        # Sem conseguir ler a matéria e sem o título ou o resumo (o que a busca já trouxe) darem
        # nenhuma pista de que fale da universidade, é mais seguro descartar do que deixar
        # "a classificar" para sempre (dá para restaurar na página).
        r = c.aplicar_verificacao(materia("Qualquer", "https://v.com/sem-resposta"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("não foi possível ler", r["motivo"].lower())
        self.assertTrue(r["verificada"])

    def test_pagina_inacessivel_mas_resumo_cita_fica(self):
        # O título não ajuda, mas o resumo que a busca trouxe já cita a universidade: fica, mesmo
        # sem conseguir ler a matéria.
        reg = materia("Qualquer", "https://v.com/sem-resposta")
        reg["resumo"] = "Durante o evento, a Unifesspa recebeu uma homenagem."
        r = c.aplicar_verificacao(reg, self.baixar)
        self.assertNotIn("descartada", r)

    def test_rede_social_e_sempre_descartada(self):
        # Instagram, Facebook e YouTube não são imprensa e, mesmo citando a universidade de verdade,
        # nunca entram no clipping.
        reg = materia("Foto no Instagram", "https://www.instagram.com/p/abc123/")
        reg["resumo"] = "Parabenizo, em nome da Unifesspa, a professora homenageada."
        r = c.aplicar_verificacao(reg, self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("rede social", r["motivo"])
        self.assertEqual(self.baixados, [], "não tenta nem ler a página de rede social")

    def test_local_de_votacao_e_descartado(self):
        self.paginas["v.com/c"] = pagina("Seção na Universidade Federal do Sul e Sudeste do Pará, 51ª zona.", titulo="Resultado das eleições 2026 em Rondon do Pará")
        r = c.aplicar_verificacao(
            materia("Resultado das eleições 2026 em Rondon do Pará", "https://v.com/c"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("incidental", r["motivo"])

    def test_mencao_so_na_bio_do_autor_e_descartada(self):
        # Coluna de opinião sobre outro assunto, que só cita a Unifesspa na credencial de quem
        # assina, no fim do texto (como de fato costuma vir, sem nada depois): não é cobertura
        # da universidade.
        recheio = "Este texto fala de literatura e não tem nenhuma relação com a universidade. " * 10
        corpo = (f"<article><h1>Coluna de livros</h1>{recheio}"
                 "Graduada em Letras pela UFPA; bacharela em Direito pela UNIFESSPA e leitora voraz, "
                 "por amor e vocação.</article>")
        self.paginas["v.com/h"] = f"<html><body>{corpo}</body></html>"
        r = c.aplicar_verificacao(materia("Coluna de livros", "https://v.com/h"), self.baixar)
        self.assertTrue(r["descartada"])
        self.assertIn("credencial", r["motivo"])

    def test_professor_da_unifesspa_nao_e_tratado_como_bio(self):
        # Cobertura de verdade sobre um professor ("o professor Fulano, da Unifesspa, foi premiado")
        # não pode ser confundida com a bio de quem assina o texto.
        recheio = "Em poucas linhas: outros assuntos do dia, sem relação com a universidade. " * 10
        corpo = (f"<article><h1>Coluna de notas</h1>{recheio}"
                 "O professor Fulano de Tal, da Universidade Federal do Sul e Sudeste do Pará "
                 f"(Unifesspa), foi reconhecido com um prêmio nacional. {recheio}</article>")
        self.paginas["v.com/i"] = f"<html><body>{corpo}</body></html>"
        r = c.aplicar_verificacao(materia("Coluna de notas", "https://v.com/i"), self.baixar)
        self.assertNotIn("descartada", r)

    def test_mencao_fora_da_bio_alem_da_bio_nao_e_descartada(self):
        # Quando há menção real em outro trecho, além da bio de quem assina, a matéria fica.
        self.paginas["v.com/j"] = pagina(
            "A Unifesspa lançou um edital de bolsas para estudantes. "
            "Graduada em Letras pela UFPA; bacharela em Direito pela UNIFESSPA e leitora voraz.",
            titulo="Notícia com bio")
        r = c.aplicar_verificacao(materia("Notícia com bio", "https://v.com/j"), self.baixar)
        self.assertNotIn("descartada", r)

    def test_restaura_quando_passa_a_citar(self):
        self.paginas["v.com/d"] = pagina("A Unifesspa anunciou o resultado.", titulo="Notícia")
        reg = materia("Notícia", "https://v.com/d")
        reg["descartada"] = True
        reg["motivo"] = "O texto da matéria não cita a Unifesspa."
        self.assertNotIn("descartada", c.aplicar_verificacao(reg, self.baixar))

    def test_resumo_troca_pelo_trecho_da_mencao_em_boletim(self):
        # Coluna/boletim de vários assuntos: o resumo trazido pela busca descreve a coluna inteira
        # (o primeiro assunto, sobre outra coisa), mas o texto lido tem, mais adiante, um parágrafo
        # de verdade sobre a Unifesspa. O resumo deve passar a mostrar esse trecho, não a descrição
        # genérica da coluna.
        recheio = "Um assunto qualquer que não tem nada a ver. " * 20
        corpo = (f"<article><h1>Coluna de hoje</h1>{recheio}"
                 "Em Poucas Linhas: o professor Abílio, da Unifesspa, foi premiado pela Unicamp. "
                 f"{recheio}</article>")
        self.paginas["v.com/f"] = f"<html><body>{corpo}</body></html>"
        reg = materia("Coluna de hoje", "https://v.com/f")
        reg["resumo"] = "Mais tradicional coluna do jornalismo paraense, fala de política e economia."
        c.aplicar_verificacao(reg, self.baixar)
        self.assertNotIn("descartada", reg)
        self.assertIn("Unifesspa", reg["resumo"])
        self.assertIn("Abílio", reg["resumo"])

    def test_resumo_existente_que_ja_cita_e_preservado(self):
        # Quando o resumo que já veio da busca já fala da Unifesspa, não há por que trocar.
        self.paginas["v.com/g"] = pagina("A Unifesspa também participou.", titulo="Notícia")
        reg = materia("Notícia", "https://v.com/g")
        reg["resumo"] = "A Unifesspa lançou um edital de bolsas para estudantes de graduação."
        c.aplicar_verificacao(reg, self.baixar)
        self.assertEqual(reg["resumo"], "A Unifesspa lançou um edital de bolsas para estudantes de graduação.")

    def test_nomeia_veiculo_quando_so_havia_o_dominio(self):
        self.paginas["v.com/e"] = pagina("A Unifesspa participou do evento.", titulo="Notícia")
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
        self.paginas["correiodecarajas.com.br/x"] = pagina("A Unifesspa inaugurou o prédio.", "2026-04-02", "Prédio novo")
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

    def test_alerta_do_google(self):
        self.paginas["alerts/feeds"] = """<?xml version="1.0" encoding="utf-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
            <entry><title type="html">Ramo estudantil na &lt;b&gt;Unifesspa&lt;/b&gt; - Correio</title>
              <link href="https://www.google.com/url?rct=j&amp;url=https://correiodecarajas.com.br/ramo&amp;ct=ga"/>
              <published>2026-10-15T09:00:00Z</published>
              <content type="html">O ramo do IEEE na &lt;b&gt;Unifesspa&lt;/b&gt; come&#231;ou.</content></entry>
            <entry><title type="html">Arquivos IEEE - Correio</title>
              <link href="https://www.google.com/url?rct=j&amp;url=https://correiodecarajas.com.br/tag/ieee&amp;ct=ga"/>
              <published>2026-10-15T09:00:00Z</published><content type="html">x</content></entry>
            <entry><title type="html">Sem endereço</title>
              <link href="https://www.google.com/url?rct=j&amp;ct=ga"/>
              <published>2026-10-15T09:00:00Z</published><content type="html">y</content></entry>
            </feed>"""
        itens = c.consulta_alerta("https://www.google.com/alerts/feeds/1/2", "Unifesspa", self.baixar)
        self.assertEqual(len(itens), 1, "página de tag e entrada sem endereço ficam de fora")
        self.assertEqual(itens[0]["titulo"], "Ramo estudantil na Unifesspa - Correio")
        self.assertEqual(itens[0]["link"], "https://correiodecarajas.com.br/ramo")
        self.assertEqual(itens[0]["data"], "2026-10-15")
        self.assertEqual(itens[0]["resumo"], "O ramo do IEEE na Unifesspa começou.")

    def test_lista_paginada_nao_e_materia(self):
        self.assertFalse(c.eh_materia("https://v.com/page/6940"))
        self.assertFalse(c.eh_materia("https://v.com/?s=unifesspa"))
        self.assertTrue(c.eh_materia("https://v.com/noticia-sobre-a-unifesspa"))

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


class TestTermos(Base):
    def test_nome_de_dirigente_sozinho_nao_conta(self):
        self.assertEqual(c.mencoes(c.norm("O professor Francisco Ribeiro da Costa, de outra cidade")), 0)

    def test_nome_de_dirigente_com_a_universidade_conta(self):
        self.assertEqual(c.mencoes(c.norm("O reitor Francisco Ribeiro da Costa, da Unifesspa, assinou")), 2)

    def test_homonimo_do_reitor_e_descartado(self):
        self.paginas["v.com/hom"] = pagina("Francisco Ribeiro da Costa, vereador em outro estado, votou.",
                                           titulo="Vereador vota projeto")
        r = c.aplicar_verificacao(materia("Vereador vota projeto", "https://v.com/hom"), self.baixar)
        self.assertTrue(r["descartada"])

    def test_lista_veiculos_sem_nome(self):
        regs = [materia("A", "https://novoportal.com.br/1"), materia("B", "https://novoportal.com.br/2"),
                materia("C", "https://correiodecarajas.com.br/3")]
        for r in regs:
            r["veiculo"] = "novoportal.com.br" if "novoportal" in r["link"] else "Correio de Carajás"
        fora = materia("D", "https://outro.com/4")
        fora["descartada"] = True
        achados = c.veiculos_sem_nome(regs + [fora])
        self.assertEqual([(x["dominio"], x["materias"]) for x in achados], [("novoportal.com.br", 2)])
        self.assertEqual(achados[0]["nomes"], ["novoportal.com.br"])


class TestNomes(Base):
    def test_g1_do_para_e_separado_do_nacional(self):
        self.assertEqual(c.nome_por_endereco("https://g1.globo.com/pa/para/noticia/x"), "G1 Pará")
        self.assertEqual(c.nome_por_endereco("https://g1.globo.com/pa/santarem-regiao/noticia/x"), "G1 Pará")
        self.assertEqual(c.nome_por_endereco("https://g1.globo.com/educacao/noticia/y"), "G1")
        self.assertEqual(c.nome_por_endereco("https://g1.globo.com/pe/pernambuco/noticia/z"), "G1")

    def test_link_do_google_ainda_nao_resolvido_fica_com_o_nome_do_dominio(self):
        self.assertEqual(c.nome_por_endereco("https://news.google.com/rss/articles/ABC", "g1.globo.com"), "G1")

    def test_dominio_desconhecido_nao_ganha_nome(self):
        self.assertEqual(c.nome_por_endereco("https://sitequalquer.com.br/x"), "")

    def test_identificador_igual_ao_da_pagina(self):
        # Mesmo cálculo do javascript da página (djb2 em base 36), conferido contra ele.
        self.assertEqual(c.id_da_pagina("Matéria X", "G1"), "a92j654")
        self.assertEqual(c.id_da_pagina("Unifesspa lança curso", "Correio de Carajás"), "a16felrj")

    def test_renomear_leva_as_revisoes_junto(self):
        regs = [materia("Eleição em Rondon", "https://g1.globo.com/pa/para/noticia/1"),
                materia("Nota nacional", "https://g1.globo.com/educacao/noticia/2"),
                materia("Coluna", "https://www.zedudu.com.br/post/3")]
        regs[0]["veiculo"] = "g1"; regs[0]["dominio"] = "g1.globo.com"
        regs[1]["veiculo"] = "G1"; regs[1]["dominio"] = "g1.globo.com"
        regs[2]["veiculo"] = "ZÉ DUDU"; regs[2]["dominio"] = "zedudu.com.br"
        revisoes = {"over": {c.id_da_pagina("Eleição em Rondon", "g1"): {"tom": "Neutro"}},
                    "veiculos": {"g1": "Nacional"}, "manuais": []}
        mudancas = c.renomear_veiculos(regs, revisoes)
        self.assertEqual([r["veiculo"] for r in regs], ["G1 Pará", "G1", "Zé Dudu"])
        self.assertIn(c.id_da_pagina("Eleição em Rondon", "G1 Pará"), revisoes["over"],
                      "o tom classificado tem de acompanhar o novo nome")
        self.assertEqual(revisoes["over"][c.id_da_pagina("Eleição em Rondon", "G1 Pará")]["tom"], "Neutro")
        self.assertEqual(revisoes["veiculos"], {"G1 Pará": "Nacional"})
        self.assertEqual(sum(m[2] for m in mudancas), 2)

    def test_renomear_de_ponta_a_ponta(self):
        reg = materia("Coluna", "https://www.zedudu.com.br/post/3")
        reg["veiculo"] = "ZÉ DUDU"; reg["dominio"] = "zedudu.com.br"
        c.JSON_PATH.write_text(json.dumps([reg]), encoding="utf-8")
        c.REVISOES_PATH.write_text(json.dumps(
            {"over": {c.id_da_pagina("Coluna", "ZÉ DUDU"): {"tom": "Positivo"}}, "veiculos": {}, "manuais": []}),
            encoding="utf-8")
        self.assertEqual(c.principal(["--renomear"], self.baixar), 0)
        self.assertEqual(json.loads(c.JSON_PATH.read_text(encoding="utf-8"))[0]["veiculo"], "Zé Dudu")
        rev = json.loads(c.REVISOES_PATH.read_text(encoding="utf-8"))
        self.assertIn(c.id_da_pagina("Coluna", "Zé Dudu"), rev["over"])
        self.assertEqual(len(self.baixados), 0, "renomear não acessa a internet")


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

    def test_planilha_tem_coluna_de_resumo(self):
        reg = materia("Com resumo", "https://v.com/a")
        reg["resumo"] = "Primeiras linhas da matéria."
        c.salvar([reg])
        planilha = c.CSV_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("Resumo", planilha.splitlines()[0])
        self.assertIn("Primeiras linhas da matéria.", planilha)


class TestRotina(Base):
    def test_reverificacao_limpa_o_que_ja_estava_salvo(self):
        self.paginas["v.com/a"] = pagina("A Unifesspa divulgou o edital.", titulo="Edital")
        self.paginas["v.com/b"] = pagina("Vestibular Unifesp 2027.", titulo="Vestibular Unifesp")
        c.JSON_PATH.write_text(json.dumps([materia("Edital", "https://v.com/a"),
                                           materia("Vestibular Unifesp", "https://v.com/b")]),
                               encoding="utf-8")
        self.assertEqual(c.principal(["--reverificar"], self.baixar), 0)
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertTrue(salvos["Vestibular Unifesp"]["descartada"])
        self.assertNotIn("descartada", salvos["Edital"])
        self.assertTrue(c.CACHE_PATH.exists(), "o cache evita reler tudo na próxima vez")

    def test_reverificacao_revisa_descartadas_e_listas_eleitorais(self):
        self.paginas["v.com/a"] = pagina("Agora a Unifesspa aparece no texto.", titulo="Voltou")
        self.paginas["v.com/c"] = pagina("Seção na Universidade Federal do Sul e Sudeste do Pará, 51ª zona.",
                                         titulo="Resultado das eleições 2026 em Xinguara")
        antiga = materia("Voltou", "https://v.com/a")
        antiga.update({"verificada": True, "descartada": True,
                       "motivo": "O texto da matéria não cita a Unifesspa."})
        eleicao = materia("Resultado das eleições 2026 em Xinguara", "https://v.com/c")
        eleicao.update({"verificada": True, "mencoes": 2})
        c.JSON_PATH.write_text(json.dumps([antiga, eleicao]), encoding="utf-8")
        self.assertEqual(c.principal(["--reverificar"], self.baixar), 0)
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertNotIn("descartada", salvos["Voltou"], "descarte antigo é revisto quando a leitura melhora")
        self.assertTrue(salvos["Resultado das eleições 2026 em Xinguara"]["descartada"],
                        "lista de locais de votação sai, mesmo citando a universidade mais de uma vez")

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
            self.paginas["correiodecarajas.com.br/novo"] = pagina("A Unifesspa lançou o curso.", titulo="Unifesspa lança curso")
            self.paginas["blog.com/unifesp"] = pagina("A Unifesp, de São Paulo, abriu inscrições.", titulo="Vestibular Unifesp 2027")
            self.assertEqual(c.principal(["--dias", "1", "--fontes", "google"], self.baixar), 0)
        finally:
            c.TERMOS = termos
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertNotIn("descartada", salvos["Unifesspa lança curso"])
        self.assertTrue(salvos["Vestibular Unifesp 2027"]["descartada"])
        numeros = json.loads(c.RESUMO_PATH.read_text(encoding="utf-8"))
        self.assertEqual(numeros["novas"], 2)
        self.assertEqual(numeros["descartadas"], 1)

    def test_coleta_ignora_links_de_rede_social(self):
        termos = c.TERMOS
        c.TERMOS = ["Unifesspa"]
        try:
            self.paginas["news.google.com/rss/search"] = f"""<rss><channel>
                <item><title>Unifesspa lança curso - Correio de Carajás</title>
                  <link>https://correiodecarajas.com.br/novo</link>
                  <pubDate>{date.today().strftime('%a, %d %b %Y')} 12:00:00 GMT</pubDate>
                  <source url="https://correiodecarajas.com.br">Correio de Carajás</source></item>
                <item><title>Foto da Unifesspa - Instagram</title>
                  <link>https://www.instagram.com/p/xyz987/</link>
                  <pubDate>{date.today().strftime('%a, %d %b %Y')} 12:00:00 GMT</pubDate>
                  <source url="https://www.instagram.com">Instagram</source></item>
                </channel></rss>"""
            self.paginas["correiodecarajas.com.br/novo"] = pagina("A Unifesspa lançou o curso.", titulo="Unifesspa lança curso")
            self.assertEqual(c.principal(["--dias", "1", "--fontes", "google"], self.baixar), 0)
        finally:
            c.TERMOS = termos
        salvos = {r["titulo"]: r for r in json.loads(c.JSON_PATH.read_text(encoding="utf-8"))}
        self.assertIn("Unifesspa lança curso", salvos)
        self.assertNotIn("Foto da Unifesspa", salvos, "link de rede social nem chega a ser salvo")
        self.assertFalse(any("instagram.com" in url for url in self.baixados),
                          "nunca tenta ler a página do Instagram")

    def test_falha_quando_quase_tudo_da_erro(self):
        termos = c.TERMOS
        c.TERMOS = ["Unifesspa"]
        try:
            self.assertEqual(c.principal(["--dias", "1", "--fontes", "google"], self.baixar), 1)
        finally:
            c.TERMOS = termos


if __name__ == "__main__":
    unittest.main(verbosity=2)
