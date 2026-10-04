# Transição do MVP pediátrico para o motor de análise

Data: 04/10/2026. **Estado: direção arquitetural confirmada; contratos em detalhamento.
Esta rodada formaliza documentos, sem implementação.**

## 1. A mudança de direção

O Jutta-ped passa a ser desenhado como um motor de análise extensível. A detecção de criança
desacompanhada continua sendo o primeiro caso de uso e a referência de validação, mas deixa de
definir toda a estrutura do serviço. Novos especialistas fornecem atributos; receitas e regras
combinam esses atributos para produzir alertas.

Essa é uma mudança de arquitetura e de configuração do produto, além de uma otimização de
inferência. Instalar um especialista, habilitar uma receita e acrescentar capacidade de GPU são
operações diferentes e precisam de contratos próprios.

**Objetivo confirmado: preparar o sistema-mãe para receber especialistas desenvolvidos
separadamente e migrar a pediatria para esse desenho como primeiro caso real.** A medição da
pediatria serve de referência para essa migração. O foco do trabalho é a arquitetura de plugins
e nós, com responsabilidades estáveis no sistema-mãe.

Neste documento, **sistema-mãe** significa o host do motor de análise que estamos construindo
neste projeto: ele administra câmeras, especialistas, receitas, execução e alertas. A integração
futura com o WebGuardião Core é uma fronteira externa própria; não é pré-requisito para o host
funcionar standalone.

Este documento organiza a transição. O [desenho do motor](motor_de_analise.md) continua sendo a
referência das decisões já registradas. O [prompt de discussão](prompt_discussao_motor.md) continua
sendo o roteiro para a revisão externa. Este texto incorpora o retorno sobre a primeira revisão;
o detalhamento dos contratos permanece em discussão.

### 1.1 Divisão do desenvolvimento

A intenção é ter, depois de estabelecer o contrato, um projeto/workspace separado no VS Code
para desenvolver os especialistas. Isso significa separar seu ciclo de desenvolvimento do
sistema-mãe; não exige que cada especialista seja um serviço independente ou tenha um VS Code
próprio. A criação da pasta, do workspace e eventual repositório fica para uma etapa posterior.

| Área | Responsabilidade |
|---|---|
| Sistema-mãe, neste projeto | Câmeras, captura, mestre, rastreamento, identidade das observações, catálogo e instalação, executor, cache com validade, receitas, regras, alertas, evidências, painel e métricas |
| Projeto de especialistas | Preparar e validar modelos, manifesto, atributos de saída, parâmetros e exemplos de entrada/saída; produzir pacotes compatíveis com o contrato publicado pelo sistema-mãe |
| Nós de execução | Executar especialistas compatíveis sob o orçamento e as identidades recebidos, devolver atributos e telemetria; o sistema-mãe continua responsável por regras e alertas |

Na primeira versão, o projeto de especialistas produz pacotes declarativos da equipe. Código de
treino, exportação e avaliação pode existir nesse projeto de desenvolvimento; o pacote instalado
no sistema-mãe contém apenas os artefatos permitidos pelo contrato. Lógica de execução especial
ainda necessária no host permanece no repositório do sistema-mãe, revisada e versionada.

Um novo especialista compatível com os tipos de entrada, saída e execução já suportados deve
poder ser instalado e usado por uma receita sem editar o código do sistema-mãe. Um novo tipo
de execução ou capacidade ainda não suportada exige evolução explícita do contrato e do host.
Essa é a fronteira de extensibilidade pretendida para a primeira versão.

### 1.2 Como a pediatria entra no desenho novo

A migração decompõe o comportamento atual em responsabilidades:

| Parte atual | Destino no desenho novo |
|---|---|
| Captura, detecção de pessoa e rastreamento | Infraestrutura comum do sistema-mãe |
| Classificação criança/adulto | Primeiro especialista do motor, produzindo atributos pelo mesmo contrato previsto para os demais |
| Geometria, contexto e memória usados na classificação/companhia | Mapear explicitamente para extratores e estado temporal do núcleo durante a migração, preservando sua contribuição ao resultado |
| Companhia, persistência e condições de risco | Regras e parâmetros da receita Pediatria avaliados no sistema-mãe |
| Popup, overlay, evidência e ciclo do alerta | Saídas comuns do sistema-mãe, com compatibilidade para os consumidores atuais |

“Mover a pediatria” abrange essa decomposição. Empacotar apenas os pesos e manter o restante
do fluxo dependente de um caminho pediátrico especial não conclui a migração. O especialista
produz atributos; a receita Pediatria reúne as condições que levam ao alerta.

### 1.3 O que significa o sistema-mãe estar pronto

O marco de prontidão para desenvolver especialistas separadamente exige:

1. Manual e contrato versionado de manifesto, entradas, atributos, erros, validade e compatibilidade.
2. Ciclo funcional de validar, instalar, habilitar em receita, executar, desabilitar e voltar à
   versão anterior de um pacote declarativo.
3. Pediatria migrada e comparada ao caminho anterior em modo sombra, com diferenças de qualidade
   e custo explicadas e critérios de aceitação atendidos.
4. Um segundo pacote de teste da equipe, sem finalidade operacional, demonstrando que catálogo,
   execução e consumo de atributos não dependem do nome ou de condições especiais da pediatria.
5. Exemplos e verificações de conformidade utilizáveis no projeto separado, sem acesso às
   estruturas internas do host.

Esses itens são critérios futuros, não funcionalidades declaradas como prontas hoje. Nós remotos
fazem parte do desenho e têm contrato próprio desde esta fase; a operação local com plugins
pode atingir esse marco antes da implementação e validação do transbordo remoto.

## 2. O que muda e o que está decidido

| Aspecto | Situação documentada atual | Direção do motor |
|---|---|---|
| Unidade de análise | Serviço pediátrico com modelos por sessão/câmera | Mestre, extratores de atributos e regras |
| Pediatria | Lógica específica do serviço | Receita do sistema, com comportamento a validar na migração |
| Configuração | Ativação da IA e parâmetros operacionais | Receitas por câmera, com versões e ajustes |
| Especialistas | Integrados ao código e à configuração interna | Catálogo de especialistas; instalação por pacote ainda em discussão |
| Inferência | Modelos carregados por câmera | Executor local compartilhado proposto; nós remotos em etapa posterior |
| Integração externa | Contratos de instâncias, overlays e eventos pediátricos | Evolução explícita dos contratos para acomodar o motor |

Permanecem as decisões do documento principal: caixa única desenhada pelo mestre, cascata de
análise, regras com condições obrigatórias e evidências ponderadas, tempos em segundos, receitas
por câmera e uso térmico relativo com calibração. Os fundamentos de dispositivo, cadência e
migrações são registrados como implementados naquele documento; este texto não é uma nova
auditoria do código.

## 3. Duas camadas diferentes de plugin

O [contrato existente em `plugin/`](../plugin/README.md) descreve a IA Pediatria como serviço
integrável ao WebGuardião Core: recebe referência de stream e oferece instâncias, overlays,
eventos e saúde. O empacotamento instalável ainda não está implementado.

O plugin especialista proposto na seção 8.1 do motor é interno ao processamento: recebe recortes
ou entradas declaradas e devolve atributos. Não controla câmeras, receitas ou alertas.

| Camada | Entrada e saída | Questão a fechar |
|---|---|---|
| Integração com o Core | Stream → instância, overlay, evento e saúde | Como expor o motor genérico preservando consumidores existentes? |
| Especialista do motor | Recorte/contexto → atributos tipados | Qual manifesto, ciclo de instalação e limite de execução? |
| Nó de execução | Pedido de inferência → resultado | Como negociar capacidade, versões, prazo e falhas? |

Um nó executa especialistas compatíveis; ele não constitui uma terceira camada de decisão de
alerta. Usar a palavra “plugin” nas duas primeiras camadas não torna seus manifestos intercambiáveis.

## 4. Evidências e limites do que sabemos

O [teste operacional de 03/10](testes_operacionais/2026-10-03_1352.md) documenta cerca de 500 MB
de RAM adicional por câmera, inferência OpenVINO em CPU, 14,7 fps e aproximadamente 64% de CPU
na medição de uma câmera. As fontes eram vídeos de demonstração em loop. Esses resultados
motivam revisar o custo por sessão; não dimensionam sozinhos o futuro motor em câmeras reais.

Os tempos de especialista em CUDA, o ganho por lote e a rodada de oito threads constam do prompt
e, parcialmente, do desenho do motor. O relatório operacional anexado não contém essa bateria.
Segundo o autor, essa bateria foi executada por scripts na pasta temporária da sessão, fora do
repositório. Na etapa 2, trazer a bateria para `tools/`, com modelos, resolução, duração,
aquecimento, concorrência e resultados por execução. A atribuição do gargalo ao GIL precisa de
perfil de execução; permanece uma hipótese a verificar nesta transição.

O [relatório de vídeos reais](pediatria_videos_reais_2026-06-19.md) mostra falhas de classificação
e fragmentação de trilhas. Mais capacidade de inferência não demonstra melhora de detecção.
A migração precisa avaliar qualidade dos alertas e desempenho separadamente. O modo sombra deve
comparar alertas perdidos, falsos e repetidos por troca de trilha, além de latência e consumo.

## 5. Decisões que a discussão precisa produzir

Foram aceitos os princípios de orçamento único, separação entre pedido e tentativa e expiração
por atributo descritos abaixo. O formato dos contratos e os mecanismos de execução seguem abertos.

| Tema | Resultado necessário da discussão |
|---|---|
| Manifesto do especialista | Separar versão do pacote, versão do contrato e identidade dos pesos; definir entrada, pré-processamento, atributos, validade, requisitos e parâmetros permitidos |
| Instalação | Definir origem confiável, integridade, limites de extração do ZIP, validação antes de ativar e retorno à versão anterior; checksum sozinho não identifica o autor |
| Pacotes da primeira versão | Somente declarativos fornecidos pela equipe; lógica própria permanece no repositório, sem upload de Python de terceiros. Definir validação e limites de recursos dos formatos de pesos aceitos |
| Executor local | Definir propriedade dos modelos, formação e espera máxima de lotes, orçamento de memória, fila limitada e atendimento justo entre câmeras |
| Transporte remoto | Comparar WebSocket, gRPC e fila conforme dependências offline, operação em Windows, tamanho das mensagens e controle de carga para 2–3 nós |
| Recortes | Definir formato, cor, escala, proporção e coordenadas; medir custo de codificação, banda e impacto do JPEG no especialista |
| Identidade e validade | Identificar câmera, sessão/geração, trilha, frame, especialista e configuração; impedir que respostas antigas contaminem uma nova sessão ou trilha |
| Falhas e cache | Definir expiração por atributo e comportamento indeterminado; ausência de resultado não deve ser interpretada automaticamente como ausência de risco |
| Fusão térmica | Delimitar responsabilidades do núcleo e do extrator; validar calibração por área/profundidade, sincronismo, paleta e perda de uma fonte |
| Observabilidade | Definir indicadores, janelas de avaliação e critérios dos estados saudável, atenção, crítico e sem dados |

Adota-se um prazo por observação, contado desde a captura, com idade máxima definida por atributo
e receita. Fila, lote, transmissão e inferência consomem esse mesmo orçamento; uma nova tentativa
não reinicia o prazo. Os antigos 150/300/500 ms deixam de ser limites propostos independentes.
Falta definir a representação do prazo entre máquinas e o compartilhamento de uma observação
entre receitas com tolerâncias diferentes; cada receita deve respeitar sua própria validade.

O nó pode continuar executando após a desconexão enquanto o local refaz o trabalho. O contrato
distingue pedido lógico e tentativa, admite trabalho duplicado e aceita no máximo um resultado
válido por pedido. Resultados antigos não substituem observações mais recentes do mesmo atributo.
O cache expira por atributo e receita; valor expirado ou ausente vira indeterminado. A política
de uma regra diante de indeterminação precisa ser explícita, inclusive para alertas já ativos.

Os selos de saúde não devem depender apenas de conectividade. A revisão deve contemplar idade
das observações usadas nas regras, espera de fila, latência completa p50/p95, expiração,
descartes, uso de cache, disponibilidade por câmera e consumo de recursos. Limites numéricos
ficam pendentes de medição e da tolerância operacional definida para cada receita.

## 6. Compatibilidade que precisa ser planejada

Os schemas atuais ainda expressam pediatria. Por exemplo, o
[`overlay.schema.json`](../plugin/schemas/overlay.schema.json) restringe `label` a `child`,
`adult` e `uncertain`, e fixa `plugin_id` em `ia.pediatria`. O
[`event.schema.json`](../plugin/schemas/event.schema.json) também fixa essa identidade e
enumera estados pediátricos. A caixa genérica “analisando” não cabe diretamente nesse contrato
como novo rótulo.

A discussão deve escolher entre adaptação de compatibilidade, nova versão do contrato ou ambos,
com exemplos de entrada e saída e critérios de migração. O ciclo de risco do evento e o ciclo
operacional de reconhecimento/resolução também precisam de um mapeamento explícito.

O [`config.schema.json`](../plugin/schemas/config.schema.json) e o
[`ui.schema.json`](../plugin/schemas/ui.schema.json) mantêm parâmetros finos fora da interface
externa. Receitas ajustáveis exigem definir quem pode editar cada parâmetro, suas faixas e como
uma alteração gera versão rastreável. Não basta expor todos os campos do manifesto na tela.

O [documento do painel](painel_web.md) e o [documento da API](pediatria_service_api.md) contêm
descrições de etapas anteriores. Depois de fechar a arquitetura, reconciliar as seções afetadas
com o estado real do código, principalmente propriedade dos modelos, configuração e endpoints.

## 7. Sequência proposta e critérios para avançar

O desenho dos plugins e dos nós pode avançar imediatamente. A bateria de referência é uma frente
independente: não bloqueia o manual, o manifesto ou o protocolo. As frentes convergem na validação
da migração e nas decisões de capacidade. A tabela organiza entregas e dependências de validação,
sem exigir terminar a medição para continuar a especificação. Esta rodada não inicia a bateria.

| Etapa | Entrega | Evidência para avançar |
|---|---|---|
| 1. Fechar o desenho | Manual do especialista, contrato do sistema-mãe, protocolo dos nós e estratégia de compatibilidade | Exemplos de sucesso, expiração, reinício, erro e atualização de versão coerentes entre si |
| 2. Estabelecer referência | Bateria reproduzível no caminho atual | Custo por câmera e resultados revisados nos vídeos reais, incluindo falhas conhecidas |
| 3. Motor mínimo local | Mestre, um especialista pediátrico e regras do sistema | Comparação em modo sombra, sem duplicar alertas operacionais; diferenças explicadas |
| 4. Executor compartilhado | Lotes e filas limitadas entre câmeras | Latência e qualidade sob carga, memória e recuperação de falha dentro das metas acordadas |
| 5. Receitas e pacotes | Versionamento, ativação controlada e retorno à configuração anterior; preparação do desenvolvimento separado | Critérios de prontidão da seção 1.3 atendidos, incluindo segundo pacote de conformidade |
| 6. Fusão térmica | Calibração e associação das duas fontes | Erro geométrico, desvio temporal e indisponibilidade tratados nos cenários reais |
| 7. Transbordo remoto | Um nó inicialmente, expansão após validação | Ganho útil sobre o executor local, com saturação, queda e resposta atrasada exercitadas |

Foi aceita a precedência de referência medida e modo sombra sobre executor compartilhado, com
transbordo remoto por último. O contrato pode nascer preparado para execução remota;
a necessidade de distribuir será demonstrada após medir o executor local. A fusão térmica pode
ter sua posição revista conforme disponibilidade das fontes e prioridade do caso de uso.

## 8. Referência proposta para a primeira versão

| Tema | Consolidação |
|---|---|
| Cenário inicial | Meta de ensaio: 4 câmeras, uma receita pediátrica e vídeos reais revisados. Quantidade de pessoas simultâneas ainda por definir |
| Tempo | Meta inicial a validar: observação com idade máxima de aproximadamente 1 s e alerta em até aproximadamente 2 s. Não são garantias demonstradas |
| Pacotes | Somente a equipe fornece pacotes declarativos; especialistas com lógica própria ficam no repositório |
| Operação | Direção proposta: standalone primeiro, contratos preparados para integração futura com o Core |
| Vídeos | Candidatos iniciais: os dois vídeos DVR documentados em 19/06; seleção final e anotação dos episódios ainda pendentes |

Os candidatos são `DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav` e
`DVR-3-Ch.0-1 ACESSO-BLINDEX.avi`. Eles permitem rever classificação e fragmentação já
documentadas, mas não constituem, sozinhos, um conjunto de aceitação aprovado. Incluir trechos
negativos com adultos e crianças acompanhadas; repetir fontes em quatro streams serve ao ensaio
de carga, sem equivaler à diversidade de quatro câmeras reais.

Para tornar a meta de 2 s verificável, falta fechar o marco inicial e o final: início do episódio
anotado ou confirmação após persistência; geração do evento ou exibição ao operador. A proposta
é medir episódio → exibição e decompor persistência, processamento e entrega. Regras com
persistência maior que 2 s exigem meta própria. Também falta definir percentil e tolerância de
falhas; até lá, os números são alvos de ensaio.

Próxima entrega documental: manual do especialista e protocolo dos nós, derivados desta divisão
de responsabilidades. A revisão externa pode refiná-los sem bloquear a formalização da direção.
Registrar cada conclusão com justificativa, impacto nos contratos e validação necessária.
Implementação, criação do workspace separado, execução da bateria e commits ficam para etapas
seguintes.
