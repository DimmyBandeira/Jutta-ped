# Lote 05c — Painel administrativo de especialistas (etapa 05)

Estado: **concluído em 04/10/2026**; commit: ver `git log` da branch ("painel admin de especialistas").
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md) · depende de [05a](05a_schema_validador.md) e
[05b](05b_instalacao_local.md). **A etapa 05 NÃO está concluída**: faltam receitas e o segundo pacote de conformidade.

## Escopo entregue

Tela de administração (somente admin) para instalar (upload .zip), ativar, reverter, remover e recuperar especialistas,
chamando o serviço `Instalador`. Documentada em [painel_web.md, seção 20](../painel_web.md#20-especialistas-administração).

Fora deste lote: receitas, vínculo com câmeras, uso do especialista ativo pelo motor, execução/carga de modelos,
permissões finas (apenas admin), assinatura de pacotes.

## Arquivos

| Arquivo | Função |
|---|---|
| `src/jutta_ped/painel/routes_especialistas.py` | Rotas; CSRF, teto de upload, auditoria, mapeamento de erros |
| `web/templates/especialistas.html` | Tela |
| `src/jutta_ped/painel/panel.py`, `web/templates/base.html` | Montagem do serviço/rotas e item de menu |
| `src/jutta_ped/especialistas/instalador.py` | `operacoes_recentes()` (leitura da trilha pelo serviço) |
| `tests/test_painel_especialistas.py` | 15 testes |
| `tests/test_painel_navegador_qt.py` | menu do admin atualizado (+ "Especialistas") |

## Validação executada (Windows, Python 3.12.7)

* `tests/test_painel_especialistas.py`: **15 passaram** — anônimo e usuário comum barrados, POST sem CSRF recusado,
  upload válido (inativo, auditado, upload temporário apagado), pacote reprovado com relatório e sem instalar, arquivo
  que não é zip, nome de arquivo malicioso, upload acima do limite (413), HTML do pacote escapado, operação concorrente
  (409), ativar/reverter/remover pelo painel, versão adulterada recusada, recuperação com registro corrompido.
* Suíte completa: **486 passaram, 6 puladas** (testes de navegador Edge, puladas como antes). O teste real em Qt
  WebEngine do menu do admin passa com o novo item.
* **Não** foi feita revisão visual da tela num navegador: o HTML foi validado por testes de resposta, não por captura.

## Correção após revisão externa (04/10/2026)

O limite de upload era uma dependência do FastAPI, que roda **depois** de o corpo multipart ser lido (reproduzido: 413
com o parser já acionado e o arquivo recebido). Foi substituído por um middleware ASGI (`painel/limite_corpo.py`):
recusa por `Content-Length` sem ler o corpo; sem o cabeçalho (ou com valor falso) conta os bytes e interrompe a
leitura ao passar do teto (entrega `http.disconnect` ao aplicativo e descarta qualquer resposta posterior). Testes:
parser multipart não acionado (espiado), corpo em pedaços, cabeçalho mentiroso, contagem exata de leituras (3 de 10
pedaços), outras rotas e corpo dentro do teto intactos.

## Limitações

* Ativar não afeta a análise: o motor ainda não lê o especialista ativo.
* Upload de até 600 MiB passa pelo parser multipart (disco temporário do servidor) antes da rota. O teto é aplicado
  por middleware ASGI **antes do parser** (`Content-Length` e contagem de bytes recebidos; ver correção abaixo).
* Instalação é síncrona (a requisição espera validar e extrair); pacotes grandes ocupam uma thread do servidor.
* O painel usa um único `Instalador` por processo; vários processos do painel na mesma pasta se protegem pelo lock do SO.
* Mensagens do serviço de instalação estão sem acento (convenção dos serviços); a tela as repete como recebidas.

## Próximo lote recomendado

Receitas (etapa 05): vincular especialistas ativos a regras e a câmeras, com versão registrada nos alertas. Em paralelo e
independente, a bateria de referência (etapa 02). Nós remotos seguem aguardando A1–A4.
