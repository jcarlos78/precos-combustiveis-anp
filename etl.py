"""Baixa a Série Histórica de Preços de Combustíveis e de GLP (ANP) e gera
tabelas agregadas em Parquet para o dashboard.

Uso:
    python etl.py

Os semestres já processados ficam em cache (data/cache/), então rodar de novo
só baixa os arquivos recentes e o que tiver falhado antes.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

PAGINA = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "serie-historica-de-precos-de-combustiveis"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/codeforamerica/click_that_hood/"
    "master/public/data/brazil-states.geojson"
)
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
TENTATIVAS = 40

DATA = Path(__file__).parent / "data"
CACHE = DATA / "cache"
TMP = DATA / "tmp"
PARALELOS = 4

# Os arquivos têm sempre as mesmas 16 colunas, na mesma ordem; os nomes do
# cabeçalho variam (acentos, BOM), então lemos por posição.
COLUNAS = {
    0: "regiao",
    1: "uf",
    2: "municipio",
    4: "cnpj",
    10: "produto",
    11: "data",
    12: "venda",
    13: "compra",
    15: "bandeira",
}


def listar_arquivos() -> dict[str, list[str]]:
    """Lê a página da ANP e devolve as URLs por grupo (dsas, dsan, qus)."""
    html = requests.get(PAGINA, headers=HEADERS, timeout=120).text
    urls = sorted(set(re.findall(r'href="([^"]*arquivos/shpc/[^"]+)"', html)))
    grupos: dict[str, list[str]] = {"dsas": [], "dsan": [], "qus": []}
    for url in urls:
        m = re.search(r"/shpc/(dsas|dsan|qus)/", url)
        if m:
            grupos[m.group(1)].append(url)
    return grupos


def baixar(url: str) -> Path:
    """Baixa para data/tmp e devolve o caminho; quem chama apaga depois."""
    destino = TMP / url.split("/shpc/")[1].replace("/", "__")
    # O servidor da ANP costuma derrubar a conexão no meio dos arquivos
    # grandes, então retomamos com Range até completar.
    destino.unlink(missing_ok=True)
    total = None
    for _ in range(TENTATIVAS):
        ja = destino.stat().st_size if destino.exists() else 0
        try:
            cab = dict(HEADERS, Range=f"bytes={ja}-") if ja else HEADERS
            with requests.get(url, headers=cab, timeout=120, stream=True) as r:
                r.raise_for_status()
                if ja and r.status_code != 206:  # servidor ignorou o Range
                    ja = 0
                if total is None and "Content-Length" in r.headers:
                    total = ja + int(r.headers["Content-Length"])
                with destino.open("ab" if ja else "wb") as f:
                    for bloco in r.iter_content(1 << 20):
                        f.write(bloco)
        except requests.RequestException:
            time.sleep(2)
            continue
        if total is None or destino.stat().st_size >= total:
            return destino
    destino.unlink(missing_ok=True)
    raise OSError(f"download incompleto após {TENTATIVAS} tentativas")


def baixar_e_ler(url: str) -> pd.DataFrame:
    bruto = baixar(url)
    try:
        df = ler(bruto)
    finally:
        bruto.unlink(missing_ok=True)
    return df


def semestral(url: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    """Agregados de um arquivo semestral, com cache em data/cache.

    Os brutos somam vários GB; guardamos só o agregado de cada um.
    """
    base = CACHE / url.split("/shpc/")[1].replace("/", "__")
    c_uf, c_mun, c_meta = (base.with_name(base.name + s) for s in (".uf.parquet", ".mun.parquet", ".json"))
    if c_meta.exists():
        fim = pd.Timestamp(json.loads(c_meta.read_text())["ultima_data"])
        return pd.read_parquet(c_uf), pd.read_parquet(c_mun), fim
    df = baixar_e_ler(url)
    uf, mun = agregar(df)
    fim = df["data"].max()
    uf.to_parquet(c_uf, index=False)
    mun.to_parquet(c_mun, index=False)
    c_meta.write_text(json.dumps({"ultima_data": fim.isoformat(), "linhas": len(df)}))
    return uf, mun, fim


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(
                io.BytesIO(conteudo),
                sep=";",
                header=0,
                usecols=list(COLUNAS),
                dtype=str,
                encoding=enc,
                on_bad_lines="skip",
            )
            break
        except UnicodeDecodeError:
            continue
    df.columns = [COLUNAS[i] for i in sorted(COLUNAS)]
    return df


def ler(caminho: Path) -> pd.DataFrame:
    conteudo = caminho.read_bytes()
    if conteudo[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
            partes = [_ler_csv(z.read(n)) for n in z.namelist() if not n.endswith("/")]
        df = pd.concat(partes, ignore_index=True)
    else:
        df = _ler_csv(conteudo)

    df["data"] = pd.to_datetime(df["data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    for col in ("venda", "compra"):
        df[col] = pd.to_numeric(
            df[col].str.strip().str.replace(",", ".", regex=False), errors="coerce"
        )
    for col in ("regiao", "uf", "municipio", "produto", "bandeira"):
        df[col] = df[col].str.strip().str.upper()
    df["cnpj"] = df["cnpj"].str.replace(r"\D", "", regex=True)
    df = df.dropna(subset=["data", "venda", "uf", "produto"])
    df = df[df["venda"] > 0]
    df["bandeira"] = df["bandeira"].fillna("NÃO INFORMADA")
    return df


def agregar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devolve (semana × UF × produto × bandeira, mês × município × produto)."""
    df = df.assign(
        semana=df["data"].dt.to_period("W-SAT").dt.start_time,
        mes=df["data"].dt.to_period("M").dt.start_time,
        # margem só existe quando o preço de compra foi informado
        venda_c=df["venda"].where(df["compra"].notna()),
    )
    uf = (
        df.groupby(["semana", "regiao", "uf", "produto", "bandeira"], observed=True)
        .agg(
            n=("venda", "size"),
            soma_venda=("venda", "sum"),
            min_venda=("venda", "min"),
            max_venda=("venda", "max"),
            n_compra=("compra", "count"),
            soma_compra=("compra", "sum"),
            soma_venda_c=("venda_c", "sum"),
        )
        .reset_index()
    )
    mun = (
        df.groupby(["mes", "uf", "municipio", "produto"], observed=True)
        .agg(
            n=("venda", "size"),
            soma_venda=("venda", "sum"),
            min_venda=("venda", "min"),
            max_venda=("venda", "max"),
            postos=("cnpj", "nunique"),
        )
        .reset_index()
    )
    return uf, mun


def reagrupar(partes: list[pd.DataFrame], chaves: list[str]) -> pd.DataFrame:
    df = pd.concat(partes, ignore_index=True)
    regras = {c: "sum" for c in df.columns if c not in chaves}
    regras |= {"min_venda": "min", "max_venda": "max"}
    if "postos" in regras:
        regras["postos"] = "max"
    return df.groupby(chaves, observed=True).agg(regras).reset_index()


def main() -> None:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args()

    for pasta in (CACHE, TMP):
        pasta.mkdir(parents=True, exist_ok=True)
    grupos = listar_arquivos()
    if not grupos["dsas"]:
        sys.exit("Nenhum arquivo encontrado na página da ANP; o layout pode ter mudado.")

    def familia(url: str) -> str:
        return "glp" if "glp" in url.rsplit("/", 1)[-1].lower() else "ca"

    partes_uf: list[pd.DataFrame] = []
    partes_mun: list[pd.DataFrame] = []
    falhas: list[str] = []
    # Corte por família (combustíveis automotivos × GLP) para não contar duas
    # vezes o que aparece no semestral, no mensal e nas últimas 4 semanas.
    corte = {"glp": pd.Timestamp.min, "ca": pd.Timestamp.min}

    urls = grupos["dsas"]
    with ThreadPoolExecutor(PARALELOS) as pool:
        futuros = {pool.submit(semestral, u): u for u in urls}
        for i, fut in enumerate(as_completed(futuros), 1):
            url = futuros[fut]
            nome = url.split("/shpc/")[1]
            try:
                uf, mun, fim = fut.result()
            except Exception as e:  # um arquivo quebrado não derruba a carga
                falhas.append(nome)
                print(f"[semestral {i}/{len(urls)}] {nome} IGNORADO: {e}", flush=True)
                continue
            print(f"[semestral {i}/{len(urls)}] {nome}", flush=True)
            partes_uf.append(uf)
            partes_mun.append(mun)
            corte[familia(url)] = max(corte[familia(url)], fim)

    def recentes(urls: list[str], rotulo: str) -> None:
        novos = dict(corte)
        for i, url in enumerate(urls, 1):
            nome = url.split("/shpc/")[1]
            print(f"[{rotulo} {i}/{len(urls)}] {nome}", flush=True)
            try:
                df = baixar_e_ler(url)
            except Exception as e:
                falhas.append(nome)
                print(f"    IGNORADO: {e}", flush=True)
                continue
            fam = familia(url)
            df = df[df["data"] > corte[fam]]
            if df.empty:
                continue
            novos[fam] = max(novos[fam], df["data"].max())
            uf, mun = agregar(df)
            partes_uf.append(uf)
            partes_mun.append(mun)
        corte.update(novos)

    # Mensais: só os anos ainda não cobertos por completo pelos semestrais.
    ano_min = min(corte.values()).year
    recentes(
        [u for u in grupos["dsan"] if int(re.search(r"/dsan/(\d{4})/", u).group(1)) >= ano_min],
        "mensal",
    )
    recentes(grupos["qus"], "últimas 4 semanas")

    uf = reagrupar(partes_uf, ["semana", "regiao", "uf", "produto", "bandeira"])
    mun = reagrupar(partes_mun, ["mes", "uf", "municipio", "produto"])
    for df in (uf, mun):
        for col in df.select_dtypes(["object", "string"]):
            df[col] = df[col].astype("category")

    uf.to_parquet(DATA / "precos_uf.parquet", index=False)
    mun.to_parquet(DATA / "precos_municipio.parquet", index=False)
    print(f"\nprecos_uf.parquet: {len(uf):,} linhas | {uf['semana'].min():%d/%m/%Y} a {uf['semana'].max():%d/%m/%Y}")
    print(f"precos_municipio.parquet: {len(mun):,} linhas")
    print(f"observações de preço: {int(uf['n'].sum()):,}")
    if falhas:
        print(f"\nATENÇÃO: {len(falhas)} arquivo(s) ficaram de fora; rode de novo para tentar outra vez:")
        print("\n".join(f"  {f}" for f in falhas))

    geo = DATA / "brasil_ufs.geojson"
    if not geo.exists():
        try:
            geo.write_bytes(requests.get(GEOJSON_URL, timeout=120).content)
        except requests.RequestException as e:
            print(f"Mapa não baixado ({e}); o dashboard funciona sem ele.")


if __name__ == "__main__":
    main()
