# Lote 05b — Ciclo local de instalação (antecipação da etapa 05)

Estado: **concluído em 04/10/2026**; commit: ver `git log` da branch ("ciclo local de instalacao").
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md) · depende do [lote 05a](05a_schema_validador.md).
**A etapa 05 NÃO está concluída**: faltam a instalação pelo painel (upload/telas), receitas e o segundo pacote
de conformidade.

## Escopo entregue

Validar → extrair com isolamento e limites → registrar a versão instalada → recuperar após falha → ativar →
reverter à versão anterior → remover versão inativa. Armazenamento em arquivos; contrato normativo em
[manual, seção 13](../manual_especialista.md#13-instalação-local-implementada-sem-painel).

Fora deste lote: painel (upload, telas, permissões), receitas, carga/execução de modelos, assinatura de
pacotes, reconciliação com banco, nós remotos.

## Arquivos

| Arquivo | Função |
|---|---|
| `src/jutta_ped/especialistas/instalador.py` | `Instalador`: instalar, ativar, reverter, remover, listar, recuperar, `caminho_ativo` |
| `src/jutta_ped/especialistas/cli.py` | subcomandos `instalar ativar reverter remover listar recuperar` |
| `tests/test_especialistas_instalador.py` | 22 testes |
| `src/jutta_ped/especialistas/fontes.py` | correção: link/tipo verificado antes de separar arquivo e pasta (lote 05a) |
| `tests/test_especialistas_validador.py` | +7 testes de regressão (97 no total) |

## Validação executada (Windows, Python 3.12.7)

* Instalador: **22 passaram** — instalação de ZIP e pasta, reprovado sem rastro, idempotência e versão
  imutável, limites na extração, defesa contra caminho fora do destino, ativar/trocar/reverter, versão
  corrompida (recusa, marca e pulo no retorno), remoção da ativa recusada, **quedas simuladas** após a
  extração, após mover e após registrar (a recuperação conclui ou limpa), conteúdo adulterado em quarentena,
  órfão em quarentena, ativa sem arquivos volta ao histórico, registro ilegível (`.bak` e reconstrução),
  registro atômico sob falha de E/S, exclusão mútua com lock de processo vivo e morto, ciclo completo e
  códigos da CLI.
* Suíte completa: **460 passaram, 6 puladas** (testes de navegador, puladas como antes).
* Fumaça real da CLI (`instalar`, `ativar`, `listar`) com ZIP sintético: saída 0 e registro coerente.
* As quedas foram **simuladas por injeção de falha** (`BaseException` em pontos nomeados), não matando o
  processo; travamentos reais de sistema de arquivos ou OneDrive não foram exercitados.

## Limitações

* Pesos sintéticos; nenhum modelo foi carregado. Conformidade estática não é aprovação do modelo.
* Instalação concorrente entre máquinas (pasta compartilhada) não é suportada: o lock é por processo local.
* O registro em arquivo não substitui um banco; a integração com o painel (e Alembic) é decisão futura.
* Sem política de retenção: versões antigas só saem com `remover`; `quarentena/` só é limpa manualmente.
* Autoria continua não verificada (hash é integridade).

## Próximo lote recomendado

Integrar ao painel (admin): upload → `instalar` → ativação e retorno na tela, com auditoria e permissão de admin,
lendo `registro.json`. Ou, dentro da etapa 05, o segundo pacote de conformidade, quando a migração da pediatria
(etapa 03) existir.
