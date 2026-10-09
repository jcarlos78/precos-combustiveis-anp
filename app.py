"""Dashboard da Série Histórica de Preços de Combustíveis e de GLP (ANP).

Rode `python etl.py` uma vez para gerar os dados e depois `streamlit run app.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DATA = Path(__file__).parent / "data"

# Paleta categórica em ordem fixa (a ordem é o que garante a separação para
# daltônicos); a cor segue a entidade, nunca a posição no ranking.
CORES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
AZUL = CORES[0]
SEQUENCIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CINZA = "#898781"
MAX_SERIES = len(CORES)

REGIOES = {"N": "Norte", "NE": "Nordeste", "CO": "Centro-Oeste", "SE": "Sudeste", "S": "Sul"}
COR_REGIAO = dict(zip(REGIOES.values(), CORES))
ORDEM_PRODUTOS = [
    "GASOLINA", "ETANOL", "DIESEL S10", "GLP", "GASOLINA ADITIVADA", "DIESEL", "GNV", "DIESEL S50",
]
GRANULARIDADE = {"Semanal": "W-SAT", "Mensal": "M", "Anual": "Y"}

st.set_page_config(page_title="Preços de Combustíveis · ANP", page_icon="⛽", layout="wide")


# ---------------------------------------------------------------- dados
@st.cache_data(show_spinner="Carregando dados…")
def carregar() -> tuple[pd.DataFrame, pd.DataFrame]:
    uf = pd.read_parquet(DATA / "precos_uf.parquet")
    mun = pd.read_parquet(DATA / "precos_municipio.parquet")
    uf["regiao"] = uf["regiao"].astype(str).map(REGIOES).fillna("Não informada")
    for col in ("uf", "produto", "bandeira"):
        uf[col] = uf[col].astype(str)
    for col in ("uf", "municipio", "produto"):
        mun[col] = mun[col].astype(str)
    return uf, mun


@st.cache_data
def carregar_mapa() -> dict | None:
    caminho = DATA / "brasil_ufs.geojson"
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def unidade(produto: str) -> str:
    return {"GLP": "R$/13 kg", "GNV": "R$/m³"}.get(produto, "R$/litro")


def brl(valor: float, casas: int = 2) -> str:
    if pd.isna(valor):
        return "–"
    return f"R$ {valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(valor: float) -> str | None:
    if pd.isna(valor):
        return None
    return f"{valor:+.1f}%".replace(".", ",")


def media(df: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    """Preço médio ponderado pelo número de coletas, mais mínimo e máximo."""
    g = df.groupby(chaves, observed=True).agg(
        n=("n", "sum"),
        soma=("soma_venda", "sum"),
        minimo=("min_venda", "min"),
        maximo=("max_venda", "max"),
    ).reset_index()
    g["preco"] = g["soma"] / g["n"]
    return g.drop(columns="soma")


def por_periodo(df: pd.DataFrame, freq: str, col: str = "semana") -> pd.DataFrame:
    if freq == "W-SAT":
        return df.assign(periodo=df[col])
    return df.assign(periodo=df[col].dt.to_period(freq).dt.start_time)


def estilo(fig: go.Figure, titulo_y: str = "", altura: int = 420) -> go.Figure:
    fig.update_layout(
        height=altura,
        margin=dict(l=0, r=10, t=30, b=0),
        separators=",.",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
        xaxis_title=None,
        yaxis_title=titulo_y,
    )
    fig.update_traces(selector=dict(type="scatter", mode="lines"), line_width=2)
    return fig


def mostrar(fig: go.Figure) -> None:
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------- carga
if not (DATA / "precos_uf.parquet").exists():
    st.title("⛽ Preços de Combustíveis · ANP")
    st.error("Dados ainda não gerados. Rode `python etl.py` na pasta do projeto e recarregue a página.")
    st.stop()

base, municipios = carregar()

# ---------------------------------------------------------------- filtros
with st.sidebar:
    st.header("Filtros")
    disponiveis = set(base["produto"].unique())
    produtos = [p for p in ORDEM_PRODUTOS if p in disponiveis] + sorted(disponiveis - set(ORDEM_PRODUTOS))
    produto = st.selectbox("Produto", produtos, format_func=str.title)

    do_produto = base[base["produto"] == produto]
    d_min, d_max = do_produto["semana"].min().date(), do_produto["semana"].max().date()
    inicio_padrao = max(d_min, (pd.Timestamp(d_max) - pd.DateOffset(years=5)).date())
    periodo = st.slider(
        "Período", d_min, d_max, (inicio_padrao, d_max), format="MM/YYYY",
    )

    regioes_sel = st.multiselect("Região", list(REGIOES.values()), placeholder="Todas")
    ufs_possiveis = sorted(
        do_produto.loc[do_produto["regiao"].isin(regioes_sel), "uf"].unique()
        if regioes_sel else do_produto["uf"].unique()
    )
    ufs_sel = st.multiselect("Estado", ufs_possiveis, placeholder="Todos")
    bandeiras_sel = st.multiselect(
        "Bandeira",
        do_produto.groupby("bandeira")["n"].sum().sort_values(ascending=False).index.tolist(),
        placeholder="Todas",
    )
    st.caption(
        "Fonte: [ANP · Série Histórica de Preços de Combustíveis e de GLP]"
        "(https://dados.gov.br/dados/conjuntos-dados/serie-historica-de-precos-de-combustiveis-e-de-glp). "
        "Preços nominais de revenda, sem correção pela inflação."
    )


def filtrar_geo(df: pd.DataFrame) -> pd.DataFrame:
    if regioes_sel:
        df = df[df["regiao"].isin(regioes_sel)]
    if ufs_sel:
        df = df[df["uf"].isin(ufs_sel)]
    if bandeiras_sel:
        df = df[df["bandeira"].isin(bandeiras_sel)]
    return df


ini, fim = pd.Timestamp(periodo[0]), pd.Timestamp(periodo[1])
no_periodo = base[base["semana"].between(ini, fim)]
dados = filtrar_geo(no_periodo[no_periodo["produto"] == produto])
un = unidade(produto)

# ---------------------------------------------------------------- cabeçalho
st.title("⛽ Preços de Combustíveis · ANP")
recorte = ", ".join(ufs_sel) if ufs_sel else (", ".join(regioes_sel) if regioes_sel else "Brasil")
st.caption(
    f"**{produto.title()}** · {recorte} · {ini:%d/%m/%Y} a {fim + pd.Timedelta(days=6):%d/%m/%Y} · "
    f"{int(dados['n'].sum()):,} coletas de preço".replace(",", ".")
)

if dados.empty:
    st.warning("Nenhum dado para os filtros escolhidos.")
    st.stop()

serie = media(dados, ["semana"]).set_index("semana")["preco"]
ultima = serie.index.max()
atual = serie.iloc[-1]


def variacao(dias: int) -> float:
    anterior = serie[serie.index <= ultima - pd.Timedelta(days=dias)]
    return float("nan") if anterior.empty else (atual / anterior.iloc[-1] - 1) * 100


recentes = dados[dados["semana"] > ultima - pd.Timedelta(weeks=4)]
por_uf_recente = media(recentes, ["uf"]).sort_values("preco")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(f"Preço médio ({un})", brl(atual), help=f"Semana iniciada em {ultima:%d/%m/%Y}")
c2.metric("Em 4 semanas", pct(variacao(28)) or "–")
c3.metric("Em 12 meses", pct(variacao(364)) or "–")
c4.metric(
    "Estado mais barato", por_uf_recente.iloc[0]["uf"],
    brl(por_uf_recente.iloc[0]["preco"]), delta_color="off", help="Média das últimas 4 semanas do período",
)
c5.metric(
    "Estado mais caro", por_uf_recente.iloc[-1]["uf"],
    brl(por_uf_recente.iloc[-1]["preco"]), delta_color="off", help="Média das últimas 4 semanas do período",
)

abas = st.tabs(
    ["Evolução", "Estados", "Bandeiras", "Margem de revenda", "Etanol × Gasolina", "Produtos", "Municípios"]
)

# ---------------------------------------------------------------- evolução
with abas[0]:
    a, b = st.columns([1, 1])
    gran = a.segmented_control("Granularidade", list(GRANULARIDADE), default="Mensal") or "Mensal"
    quebra = b.segmented_control("Comparar por", ["Total", "Região", "Estado"], default="Total") or "Total"
    dp = por_periodo(dados, GRANULARIDADE[gran])

    if quebra == "Total":
        t = media(dp, ["periodo"])
        fig = go.Figure()
        fig.add_scatter(x=t["periodo"], y=t["maximo"], mode="lines", line_width=0, showlegend=False,
                        hoverinfo="skip")
        fig.add_scatter(x=t["periodo"], y=t["minimo"], mode="lines", line_width=0, fill="tonexty",
                        fillcolor="rgba(42,120,214,0.12)", name="Faixa mín.–máx.", hoverinfo="skip")
        fig.add_scatter(
            x=t["periodo"], y=t["preco"], mode="lines", name="Preço médio", line_color=AZUL,
            customdata=t[["minimo", "maximo", "n"]],
            hovertemplate="Médio %{y:.3f}<br>Mínimo %{customdata[0]:.3f}<br>Máximo %{customdata[1]:.3f}"
                          "<br>%{customdata[2]:,} coletas<extra></extra>",
        )
        mostrar(estilo(fig, un))
        tabela = t.rename(columns={"periodo": "Período", "preco": "Médio", "minimo": "Mínimo",
                                   "maximo": "Máximo", "n": "Coletas"})
    else:
        col = "regiao" if quebra == "Região" else "uf"
        t = media(dp, ["periodo", col])
        if col == "regiao":
            mapa_cor = COR_REGIAO
        else:
            # mais de 8 séries não cabem na paleta: ficam as de maior amostra
            maiores = sorted(dp.groupby("uf")["n"].sum().nlargest(MAX_SERIES).index)
            if t["uf"].nunique() > MAX_SERIES:
                st.caption(
                    f"Mostrando os {MAX_SERIES} estados com mais coletas. "
                    "Use o filtro **Estado** para escolher outros."
                )
            t = t[t["uf"].isin(maiores)]
            mapa_cor = dict(zip(maiores, CORES))
        fig = px.line(t, x="periodo", y="preco", color=col, color_discrete_map=mapa_cor,
                      category_orders={col: list(mapa_cor)})
        fig.update_traces(hovertemplate="%{y:.3f}")
        mostrar(estilo(fig, un))
        tabela = t.pivot(index="periodo", columns=col, values="preco").reset_index().rename(
            columns={"periodo": "Período"})

    with st.expander("Ver dados"):
        st.dataframe(tabela, use_container_width=True, hide_index=True,
                     column_config={"Período": st.column_config.DateColumn(format="DD/MM/YYYY")})

# ---------------------------------------------------------------- estados
with abas[1]:
    st.caption(f"Preço médio nas últimas 4 semanas do período (até {ultima + pd.Timedelta(days=6):%d/%m/%Y}).")
    ranking = por_uf_recente.sort_values("preco", ascending=False)
    geo = carregar_mapa()
    esq, dir_ = st.columns([1, 1]) if geo else (None, st.container())
    if geo:
        with esq:
            fig = px.choropleth(
                ranking, geojson=geo, locations="uf", featureidkey="properties.sigla", color="preco",
                color_continuous_scale=SEQUENCIAL, custom_data=["uf", "preco", "n"],
            )
            fig.update_traces(
                marker_line_color="white", marker_line_width=1,
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]:.3f} " + un
                              + "<br>%{customdata[2]:,} coletas<extra></extra>",
            )
            fig.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
            fig.update_layout(height=560, margin=dict(l=0, r=0, t=0, b=0), separators=",.",
                              coloraxis_colorbar=dict(title=un, thickness=12, len=0.6))
            mostrar(fig)
    with dir_:
        fig = px.bar(ranking.sort_values("preco"), x="preco", y="uf", orientation="h", text="preco",
                     custom_data=["minimo", "maximo", "n"])
        fig.update_traces(
            marker_color=AZUL, texttemplate="%{x:.2f}", textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>Médio %{x:.3f}<br>Mínimo %{customdata[0]:.3f}"
                          "<br>Máximo %{customdata[1]:.3f}<br>%{customdata[2]:,} coletas<extra></extra>",
        )
        estilo(fig, altura=560)
        fig.update_layout(hovermode="closest", xaxis_title=un, bargap=0.25)
        mostrar(fig)

# ---------------------------------------------------------------- bandeiras
with abas[2]:
    st.caption("Bandeiras com mais coletas no período; as demais ficam agrupadas em “Outras”.")
    top = dados.groupby("bandeira")["n"].sum().nlargest(10).index
    db = dados.assign(bandeira=dados["bandeira"].where(dados["bandeira"].isin(top), "OUTRAS"))
    t = media(db, ["bandeira"])
    t["participacao"] = t["n"] / t["n"].sum() * 100
    esq, dir_ = st.columns(2)
    with esq:
        st.subheader("Preço médio", anchor=False)
        o = t.sort_values("preco")
        fig = px.bar(o, x="preco", y="bandeira", orientation="h", text="preco")
        fig.update_traces(marker_color=AZUL, texttemplate="%{x:.2f}", textposition="outside",
                          cliponaxis=False, hovertemplate="<b>%{y}</b><br>%{x:.3f} " + un + "<extra></extra>")
        # eixo cortado perto do menor valor deixaria diferenças de centavos enormes
        fig.update_xaxes(range=[0, o["preco"].max() * 1.12])
        estilo(fig, altura=440)
        fig.update_layout(hovermode="closest", xaxis_title=un, yaxis_title=None, bargap=0.25)
        mostrar(fig)
    with dir_:
        st.subheader("Participação nas coletas", anchor=False)
        o = t.sort_values("participacao")
        fig = px.bar(o, x="participacao", y="bandeira", orientation="h", text="participacao")
        fig.update_traces(marker_color=AZUL, texttemplate="%{x:.1f}%", textposition="outside",
                          cliponaxis=False, hovertemplate="<b>%{y}</b><br>%{x:.1f}% das coletas<extra></extra>")
        fig.update_xaxes(range=[0, o["participacao"].max() * 1.15])
        estilo(fig, altura=440)
        fig.update_layout(hovermode="closest", xaxis_title="% das coletas", yaxis_title=None, bargap=0.25)
        mostrar(fig)

# ---------------------------------------------------------------- margem
with abas[3]:
    com_compra = dados[dados["n_compra"] > 0]
    if com_compra.empty:
        st.info(
            "A ANP não divulga o preço de compra (distribuidora → revenda) no período escolhido. "
            "Esse dado existe na série até agosto de 2020; amplie o período para ver a margem."
        )
    else:
        m = por_periodo(com_compra, "M").groupby("periodo").agg(
            venda=("soma_venda_c", "sum"), compra=("soma_compra", "sum"), n=("n_compra", "sum")
        ).reset_index()
        m["venda"], m["compra"] = m["venda"] / m["n"], m["compra"] / m["n"]
        m["margem"] = m["venda"] - m["compra"]
        m["margem_pct"] = m["margem"] / m["venda"] * 100
        st.caption(
            "Só coletas em que o posto informou o preço de compra "
            f"(disponível até {com_compra['semana'].max():%m/%Y}). Margem bruta = venda − compra."
        )
        fig = go.Figure()
        fig.add_scatter(x=m["periodo"], y=m["venda"], mode="lines", name="Preço de venda",
                        line_color=CORES[0], hovertemplate="%{y:.3f}")
        fig.add_scatter(x=m["periodo"], y=m["compra"], mode="lines", name="Preço de compra",
                        line_color=CORES[1], hovertemplate="%{y:.3f}")
        mostrar(estilo(fig, un, altura=340))
        fig = go.Figure()
        fig.add_scatter(x=m["periodo"], y=m["margem"], mode="lines", name="Margem bruta",
                        line_color=AZUL, customdata=m[["margem_pct"]],
                        hovertemplate="%{y:.3f} " + un + " · %{customdata[0]:.1f}% do preço de venda<extra></extra>")
        fig.update_yaxes(rangemode="tozero")
        st.subheader("Margem bruta de revenda", anchor=False)
        mostrar(estilo(fig, un, altura=280))

# ---------------------------------------------------------------- etanol × gasolina
with abas[4]:
    st.caption(
        "Regra prática: abastecer com etanol tende a compensar quando ele custa menos de 70% do preço "
        "da gasolina comum. Vale para os filtros de período, região e estado; ignora o produto escolhido."
    )
    eg = filtrar_geo(no_periodo[no_periodo["produto"].isin(["ETANOL", "GASOLINA"])])
    if eg["produto"].nunique() < 2:
        st.info("Não há coletas de etanol e de gasolina para os filtros escolhidos.")
    else:
        def paridade(df: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
            p = media(df, chaves + ["produto"]).pivot(index=chaves, columns="produto", values="preco")
            p["razao"] = p["ETANOL"] / p["GASOLINA"] * 100
            return p.dropna(subset="razao").reset_index()

        t = paridade(por_periodo(eg, "M"), ["periodo"])
        fig = go.Figure()
        fig.add_scatter(x=t["periodo"], y=t["razao"], mode="lines", name="Etanol ÷ gasolina",
                        line_color=AZUL, customdata=t[["ETANOL", "GASOLINA"]],
                        hovertemplate="%{y:.1f}%<br>Etanol %{customdata[0]:.3f} · "
                                      "Gasolina %{customdata[1]:.3f}<extra></extra>")
        fig.add_hline(y=70, line_dash="dash", line_color=CINZA, line_width=1,
                      annotation_text="70%", annotation_position="top left")
        mostrar(estilo(fig, "Preço do etanol ÷ gasolina (%)", altura=340))

        st.subheader("Por estado, nas últimas 4 semanas do período", anchor=False)
        u = paridade(eg[eg["semana"] > eg["semana"].max() - pd.Timedelta(weeks=4)], ["uf"]).sort_values("razao")
        fig = px.bar(u, x="uf", y="razao", text="razao", custom_data=["ETANOL", "GASOLINA"])
        fig.update_traces(marker_color=AZUL, texttemplate="%{y:.0f}", textposition="outside",
                          cliponaxis=False,
                          hovertemplate="<b>%{x}</b><br>%{y:.1f}%<br>Etanol %{customdata[0]:.3f} · "
                                        "Gasolina %{customdata[1]:.3f}<extra></extra>")
        fig.add_hline(y=70, line_dash="dash", line_color=CINZA, line_width=1,
                      annotation_text="70%", annotation_position="top left")
        estilo(fig, "Etanol ÷ gasolina (%)", altura=340)
        fig.update_layout(hovermode="closest", bargap=0.25)
        mostrar(fig)

# ---------------------------------------------------------------- produtos
with abas[5]:
    st.caption(
        "Cada produto tem unidade e patamar de preço diferentes; por isso a comparação é em índice: "
        "100 = preço médio no primeiro mês do período. Ignora o produto escolhido no filtro."
    )
    tp = media(por_periodo(filtrar_geo(no_periodo), "M"), ["periodo", "produto"])
    tp = tp[tp["produto"].isin(produtos[:MAX_SERIES])].sort_values("periodo")
    tp["indice"] = tp["preco"] / tp.groupby("produto")["preco"].transform("first") * 100
    mapa_cor = dict(zip(produtos, CORES))
    fig = px.line(tp, x="periodo", y="indice", color="produto", color_discrete_map=mapa_cor,
                  category_orders={"produto": produtos}, custom_data=["preco"])
    fig.update_traces(hovertemplate="%{y:.1f} (R$ %{customdata[0]:.3f})")
    fig.for_each_trace(lambda tr: tr.update(name=tr.name.title()))
    fig.add_hline(y=100, line_color=CINZA, line_width=1)
    mostrar(estilo(fig, "Índice (início do período = 100)", altura=460))

# ---------------------------------------------------------------- municípios
with abas[6]:
    mp = municipios[(municipios["produto"] == produto) & municipios["mes"].between(ini.to_period("M").start_time, fim)]
    ufs_mun = ufs_sel or ufs_possiveis
    mp = mp[mp["uf"].isin(ufs_mun)]
    if bandeiras_sel:
        st.caption("O filtro de bandeira não se aplica a esta aba.")
    if mp.empty:
        st.info("Nenhum município com coletas para os filtros escolhidos.")
    else:
        meses = sorted(mp["mes"].unique(), reverse=True)
        a, b = st.columns([1, 2])
        mes = a.selectbox("Mês", meses, format_func=lambda d: pd.Timestamp(d).strftime("%m/%Y"))
        busca = b.text_input("Buscar município", placeholder="Ex.: Campinas")
        t = mp[mp["mes"] == mes].copy()
        if busca:
            t = t[t["municipio"].str.contains(busca.strip().upper(), regex=False)]
        t["preco"] = t["soma_venda"] / t["n"]
        t = t.sort_values("preco")[["municipio", "uf", "preco", "min_venda", "max_venda", "postos", "n"]]
        t["municipio"] = t["municipio"].str.title()
        t.columns = ["Município", "UF", "Médio", "Mínimo", "Máximo", "Postos", "Coletas"]
        st.dataframe(
            t, use_container_width=True, hide_index=True, height=520,
            column_config={
                c: st.column_config.NumberColumn(format="R$ %.2f", help=un) for c in ("Médio", "Mínimo", "Máximo")
            },
        )
        st.download_button(
            "Baixar CSV", t.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"precos_{produto.lower().replace(' ', '_')}_{pd.Timestamp(mes):%Y_%m}.csv", mime="text/csv",
        )
