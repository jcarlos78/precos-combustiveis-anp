# Preços de Combustíveis · ANP

Dashboard em Streamlit sobre a [Série Histórica de Preços de Combustíveis e de GLP](https://dados.gov.br/dados/conjuntos-dados/serie-historica-de-precos-de-combustiveis-e-de-glp) da ANP: preços de revenda de gasolina, etanol, diesel, GNV e GLP coletados semanalmente em postos de todo o país desde 2004.

## Como rodar

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python etl.py              # baixa e agrega os dados
.venv/bin/streamlit run app.py       # abre o dashboard
```

## Dados

`etl.py` lê a lista de arquivos direto da página da ANP, baixa cada um e grava dois agregados em `data/`:

| Arquivo | Grão |
|---|---|
| `precos_uf.parquet` | semana × UF × produto × bandeira |
| `precos_municipio.parquet` | mês × município × produto |

A primeira carga baixa vários GB e demora; os arquivos brutos são apagados depois de agregados e o resultado de cada semestre fica em `data/cache/`. Rodar `etl.py` de novo só baixa os meses recentes (e o que tiver falhado antes), então serve para atualizar os dados.

## Como ler os números

- O preço médio é a média simples de todas as coletas do recorte, não uma média ponderada por volume vendido.
- Os preços são nominais, sem correção pela inflação.
- O preço de compra (distribuidora → revenda), usado na aba de margem, só é divulgado até agosto de 2020.
- GLP está em R$ por botijão de 13 kg e GNV em R$/m³; os demais em R$/litro. Por isso a aba **Produtos** compara em índice (início do período = 100).
