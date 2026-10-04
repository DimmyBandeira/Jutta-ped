# Motor de análise — desenho e plano

Documento vivo. Registra o que foi decidido na discussão e o estado da implementação. Idioma e estilo do
projeto: português, sem jargão desnecessário.

A [transição arquitetural](transicao_motor_de_analise.md) registra a revisão, os critérios de
validação e as metas iniciais ainda não demonstradas. A rodada atual é somente documental.

## 1. Objetivo

Sair de um detector pediátrico fixo para um **motor genérico**: um detector mestre (pessoa) alimenta
extratores de atributos sobre os recortes, e **regras** (do sistema ou cadastradas) transformam atributos em
alertas. A pediatria ("criança desacompanhada") passa a ser um conjunto de regras do sistema, não código
especial.

**Direção confirmada:** este projeto prepara o **sistema-mãe**, host standalone do motor, para
receber especialistas como plugins. A pediatria será migrada para esse desenho: classificação
criança/adulto como especialista, companhia e risco como regras da receita, infraestrutura e
saídas sob responsabilidade do host. Sua medição é a referência da migração.

Depois de estabelecer o contrato, os especialistas terão um projeto/workspace separado no VS Code
para preparação, avaliação e empacotamento. Um pacote compatível com capacidades já suportadas
deve entrar no sistema-mãe sem alteração de seu código. A integração com o WebGuardião Core
permanece um contrato externo distinto. A [seção 1 da transição](transicao_motor_de_analise.md)
formaliza responsabilidades, migração e critérios de prontidão do sistema-mãe.

## 2. Pipeline em cascata

```
frame ─► mestre (pessoa, ~5 fps, CPU) ─► rastreamento ─► extratores por recorte ─► regras ─► alerta
                                          │                 ├ geometria (altura, proporções)
                                          │                 ├ classificador criança/adulto (v5/v6)
                                          │                 ├ pose → orientação, mão perto da cabeça…
                                          │                 ├ bebê no colo / carrinho (Bebe-no-colo.pt)
                                          │                 └ calor (só cores, fonte térmica)
                                          └─► caixa "analisando" (única caixa desenhada)
```

* Só o **mestre** desenha caixa, rotulada "analisando". A cor vai do neutro ao vermelho conforme a
  pontuação se aproxima do limiar das regras cadastradas; vermelho = alerta gerado.
* Atributos de pose são **tri-estado** (sim / não / indeterminado) com altura mínima da pessoa na imagem:
  abaixo disso o atributo é "indeterminado", nunca "não".
* Duas inferências no mesmo frame são esperadas (mestre em todo ciclo; especialistas só nos recortes, com
  cache por track e orçamento por ciclo).

## 3. Regras e pontuação por peso

Cada regra tem condições **obrigatórias** e **evidências ponderadas**. Dispara quando as obrigatórias
valem e a soma dos pesos ≥ limiar. A porcentagem da caixa "analisando" é soma ÷ limiar; faixas de pontuação
definem a criticidade (média / alta / crítica). Exemplo:

| Condição | Peso |
|---|---|
| Criança sem adulto próximo (obrigatória) | 60 |
| Calor acima do ambiente | 25 |
| Adulto de costas para a criança | 10 |
| Persistência acima de 5 s | 5 |

## 4. Receitas

Receita = conjunto nomeado de extratores + regras + parâmetros, **habilitada por câmera** na aba de
câmeras (com ajustes numéricos por câmera). A receita padrão "Pediatria" é do sistema: somente leitura,
clonável. Todo alerta registra `receita_id` e `receita_versao`. Aplicar mudança reinicia só as câmeras
afetadas.

## 5. Câmera térmica (bi-espectral)

Câmera com duas fontes na mesma câmera cadastrada (campos `termica_*`). A receita declara "Visível" ou
"Visível + térmico". Observado nos pares reais: campos de visão, resolução, fps e paleta diferem entre os
dois streams → exige **calibração por câmera** (homografia a partir de pares de pontos), sincronismo por
último frame com tolerância (~300 ms) e leitura relativa (índice de calor, não graus). Um componente de
**fusão** junta as observações por pessoa antes das regras. Sem calibração, o extrator térmico fica
desligado. Triagem de febre fica fora do escopo (precisa de stream radiométrico).

## 6. Colocação da inferência (dispositivo)

Política em `src/jutta_ped/service/placement.py`:

* modelo OpenVINO → CPU (ou `intel:gpu` se pedido);
* modelo `.pt` → `cuda:N` se houver CUDA, senão CPU;
* pedido explícito de CUDA troca os OpenVINO pelo `.pt` irmão; sem CUDA ou sem `.pt`, cai para CPU com o
  motivo registrado, sem falhar a sessão.

O dispositivo declarado agora é o que de fato executa (antes `auto` dizia `cuda:0` com modelos rodando na
CPU, e o perfil econômico de CPU não era aplicado). Meta futura: mestre na CPU, recortes/bebê na GPU com
transbordo dinâmico.

## 7. Cadência e tempos em segundos

* `SessionStartConfig.analysis_fps` (padrão 5; piso operacional 3) limita o laço de fontes **ao vivo**;
  lê sempre o frame mais recente. Arquivos nunca são limitados.
* Tempos do analisador de companhia em segundos (`CompanionshipTiming`), convertidos pela taxa efetiva
  (`CompanionshipConfig.from_seconds`). Referência dos defaults antigos: 15 fps (1 s, 1 s, 1 s, 3 s).
* `set_analysis_fps()` permite ao governador de carga ajustar a quente.

## 8. Banco e migrações

Alembic em `src/jutta_ped/painel/migracoes/`. `Database.migrar()` (chamado por `create_all`) aplica as
revisões; banco legado sem `alembic_version` recebe o carimbo da revisão 0001 e segue.

* 0001 — esquema base (o que existia).
* 0002 — alertas genéricos (`plugin`, `tipo`, `severidade`, `status`, `receita_id`, `receita_versao`) e
  segunda fonte da câmera (`termica_canal`, `termica_rtsp_url_enc`, `termica_stream_name`).

`status` do alerta: novo → reconhecido → resolvido | descartado.

## 8.1 Plugins de especialista (proposta em discussão)

Cada especialista instalável é proposto como pacote `.zip` com `manifest.json` + pesos,
fornecido somente pela equipe na primeira versão e enviado pela tela do admin. O sistema lê o manifesto e
passa a conhecer os atributos disponíveis; receitas e regras usam esses atributos.

Há três contratos distintos: integração do serviço com o Core (diretório `plugin/`), especialista
interno e nó de execução. Seus manifestos não são intercambiáveis. Os schemas atuais de integração
fixam `ia.pediatria` e rótulos pediátricos; a caixa genérica exige evolução versionada e/ou adaptador,
preservando os consumidores existentes. A estratégia exata permanece aberta.

* Manifesto: id, nome, versão e checksum; classe de entrada (recorte de qual classe do mestre, ou frame);
  atributos de saída tipados (tri-estado onde couber); modelo/formato/tamanho de entrada/dispositivo
  preferido (CPU como reserva); custo (ms/recorte, aceita lote); parâmetros ajustáveis com padrão e faixa;
  requisitos (altura mínima, fonte visível/térmica); `protocolo: 1`.
* Primeira versão: **declarativo** (manifesto + pesos, o host executa o modelo). Especialistas com
  lógica própria permanecem no repositório; não haverá upload de Python de terceiros. Ainda são
  necessários validação do pacote e dos pesos, limites de extração/recursos e retorno à versão anterior.
* Um **executor central** recebe recortes de todas as câmeras e os processa em lote (modelo carregado uma
  vez por configuração compatível). Medições exploratórias indicam ~5 ms/recorte em lote 8 contra
  13-16 ms em lote 1 na RTX 3050; os scripts estavam em pasta temporária, fora do repositório.
  Reproduzir a bateria antes de dimensionar o executor. GIL como gargalo é hipótese a perfilar.
* O plugin só produz atributos; nunca decide alerta. Mestre, rastreamento, caixa "analisando", regras e
  receitas ficam no núcleo.
* Contrato serializável e sem estado (recortes em bytes, atributos em JSON), distinguindo pedido lógico
  e tentativa. Prazo por observação desde a captura, com idade máxima por atributo e receita; fila,
  lote, transmissão e inferência consomem o mesmo orçamento, sem renovação em retentativas.
* Cache com expiração por atributo e receita: resultado ausente ou expirado vira **indeterminado**,
  nunca ausência automática de risco. A política das regras diante disso ainda precisa ser detalhada.

## 8.2 Nós de GPU (transbordo) — proposta em discussão

Outros servidores com GPU, na mesma rede privada (VLAN dedicada, offline), podem atender recortes
quando medições demonstrarem benefício sobre o executor local.

* **Local primeiro**: o transbordo deve caber no prazo restante da observação. Limiar de fila e
  estimativa de custo remoto ficam para medição; os antigos 150/300/500 ms independentes são retirados.
* Transporte proposto, ainda a comparar: nó conecta na máquina-mãe por WebSocket binário e apresenta plugins/versões/
  checksums e capacidade; cadastro e token geridos pelo admin. Recortes saem já no tamanho de entrada do
  modelo, em JPEG; nó sem estado e sem gravar em disco.
* Falhas: trabalho duplicado entre tentativas é permitido, mas no máximo um resultado válido é aceito
  por pedido lógico. Se o nó some, a execução local pode ser tentada dentro do orçamento restante.
  Respostas expiradas, de sessão/geração inválida ou anteriores ao atributo já aceito são descartadas.
  Só se usa cache ainda válido para a receita; expirado vira indeterminado. Fila limitada, descarte
  de trabalho obsoleto, justiça entre câmeras e disjuntor por nó precisam de política detalhada.
* Métricas na página Servidor: bytes e Mbit/s por nó, pedidos/s, RTT p50/p95, tempo de inferência no nó,
  % de timeouts/retornos ao local/atrasadas/descartes, % de transbordo, fila local, CPU/GPU, estado e
  divergência de versão do plugin; selo saudável/atenção/crítico por limites configuráveis; teto de banda
  por nó; histórico agregado por minuto no banco com retenção.
* Etapa posterior ao executor local central; o contrato já nasce compatível.

Saúde deve incluir idade das observações usadas nas regras, latência completa, expiração e uso de
cache por câmera, além da conectividade. Limites e estado sem dados ainda serão definidos.

## 9. Estado e ordem de execução

| Etapa | Estado |
|---|---|
| Fundamentos: dispositivo honesto + CUDA com reserva em CPU | feito |
| Fundamentos: tempos em segundos + `analysis_fps` | feito |
| Fundamentos: migrações + esquema genérico + campos da fonte térmica | feito |
| Manual do plugin + protocolo (manifesto, contrato, nós) | em discussão |
| Referência medida: bateria reproduzível em `tools/`, perfil para avaliar GIL e vídeos revisados | a fazer; necessária para comparar a migração, sem bloquear o desenho |
| Motor mínimo local: mestre, especialista pediátrico, regras e caixa "analisando", em modo sombra | a fazer; comparar alertas perdidos, falsos e repetidos por troca de trilha |
| Executor compartilhado: lotes, filas limitadas e orçamento entre câmeras | após referência e modo sombra |
| Receitas (CRUD, habilitar por câmera, ajustes, aplicação a quente) | a fazer |
| Pacotes declarativos da equipe: validação, ativação e retorno à versão anterior | a fazer |
| Telas: leitor de modelos, catálogo de extratores, regras, receitas, mosaico de 8 câmeras com tela cheia, Central de Alertas e popups em janelas, página Servidor + governador de carga | a fazer |
| Fusão térmica (calibração, sincronismo, extrator de calor) | a fazer |
| Transbordo remoto: um nó inicialmente, expansão após medir ganho e falhas | última etapa do motor |
| Fase 5: relatório, destinos (GPS Indoor/WifiPTT/Webhook), retenção de evidências | adiada |

Desenho de plugins/nós e medição de referência são frentes independentes. A medição fundamenta
a comparação da migração e o dimensionamento; não bloqueia a especificação do manifesto, do
contrato de atributos ou do protocolo dos nós. O marco de host pronto para desenvolvimento
separado exige também um segundo pacote de conformidade que exercite a extensão sem alterar
o código do host. Operação remota será validada depois do caminho local.

Referência proposta: standalone primeiro, 4 câmeras e uma receita pediátrica. Alvos de ensaio para
criança sozinha: idade máxima da observação ~1 s e alerta ~2 s, por validar e definir quanto aos
marcos de medição, persistência e percentil. Os dois vídeos DVR de 19/06 são candidatos à referência;
seleção final, revisão dos episódios e diversidade de cenas continuam pendentes.

## 10. Pendências conhecidas (não resolvidas)

* v6 rotula adulto como criança em alguns casos; mestre a 320 px deixa pessoas sem caixa.
* Popups repetidos por troca de ID de track (a trava usa câmera+estado+track); agrupar por câmera não foi
  aprovado ainda.
* `Bebe-no-colo.pt` (YOLO11s, 960 px; classes bebe_colo, bebe_conforto, carrinho, bolsa_termica,
  carrinho_perinatal) ainda sem decisão de versionamento.
