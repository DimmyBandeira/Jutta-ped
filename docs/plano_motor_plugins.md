# Plano de trabalho — sistema-mãe, especialistas e nós

Estado: **ativo — 05f com critérios atendidos em sombra (05/10/2026, sem commit, não arquivado). Referência de qualidade pediátrica em confirmação (não bloqueia o sistema-mãe genérico). Etapa 06 com itens e kit mínimo propostos, nada implementado.**
Início: 04/10/2026.

Última evidência de execução: [validação funcional do motor sobre RTSP real e três câmeras](etapas_motor/05f_configuracao_camera.md#validação-funcional-do-motor-sobre-rtsp-real-e-três-câmeras-05102026-sem-commit) (mestre/crops sem classificador pediátrico; pacote real criança/adulto + pacote de conformidade determinístico; isolamento, corridas e indeterminado por contrato). Sem commit; não afirma qualidade nem equivalência da migração. As medições dessa rodada têm limitações quantitativas (revisão em [`revisao_validacao_multicamera.md`](revisao_validacao_multicamera.md)): não decidem capacidade nem a etapa 04. A instrumentação foi corrigida; a etapa 02 serve à qualidade pediátrica e **não** bloqueia a evolução do sistema-mãe genérico nem o desenvolvimento de especialistas em workspace separado (lacunas listadas no registro 05f).

Último fechamento: [integração SimulaterDSA → go2rtc → runtime/player](fechamento_integracao_go2rtc.md).
go2rtc instalado e ensaio real aprovado; não confundir esse fechamento com a conclusão
da migração ou com a referência de qualidade. Próximas demandas partem desse registro.
Branch: `feature/motor-plugins-nos`.
Base: `2e8b06a7e7228f497a5220baad3065313e8a21be`, originada de
`feature/service-layer-evidencias-ui`.

## Objetivo e ponto de partida

Preparar o sistema-mãe para receber especialistas desenvolvidos em outro workspace, distribuídos
como pacotes. Câmeras, execução, receitas, regras, alertas e painel pertencem ao sistema-mãe.
A pediatria será migrada como primeiro caso de validação. Os nós executam o mesmo contrato dos
especialistas, com transporte e gestão de capacidade próprios.

A criação da branch preservou as alterações locais existentes: `motor_de_analise.md` modificado,
`prompt_discussao_motor.md` e `transicao_motor_de_analise.md` ainda não rastreados, além de
`src/models/Bebe-no-colo.pt`. Esses arquivos não compõem o commit-base acima. Este registro não
declara um snapshot commitado das alterações locais nem autoriza incluir o modelo em um commit.

Referências de arquitetura: [motor](motor_de_analise.md),
[transição](transicao_motor_de_analise.md) e [roteiro de revisão](prompt_discussao_motor.md).
Este plano organiza o trabalho; os contratos vigentes ficam nos documentos técnicos próprios.

## Etapas e critérios de conclusão

| Etapa | Entrega | Critério para concluir | Estado |
|---|---|---|---|
| 01 — Contratos autossuficientes | `docs/manual_especialista.md` e `docs/protocolo_nos.md` | Um autor externo consegue entender pacote, entrada, saída, validade e compatibilidade sem abrir o código da mãe; exemplos e decisões pendentes identificados | Em andamento: [registro](etapas_motor/01_contratos.md) |
| 02 — Referência reproduzível | Bateria em `tools/`, relatório e vídeos/episódios de referência identificados | Custo e qualidade atuais medidos com procedimento reproduzível; hipótese de GIL avaliada com perfil | Em andamento: custo e GIL medidos ([registro](etapas_motor/02_referencia.md), [relatório](referencia/2026-10-04/RELATORIO.md)); **qualidade bloqueada por revisão humana dos episódios** |
| 03 — Motor mínimo e migração | Mestre, especialista pediátrico e receita no contrato novo | Comparação em modo sombra com a etapa 02; alertas perdidos, falsos e repetidos avaliados; diferenças justificadas | Em andamento parcial: caminho genérico câmera → crop → especialista → receita em sombra implementado ([03](etapas_motor/03_motor_minimo.md)); Cena genérica, mestre compartilhado e regras entre trilhas/persistência em sombra ([03b](etapas_motor/03b_cena_regras.md)); adaptação do classificador atual **parcial** (pacote de papel compatível, migração não concluída); comparação de qualidade pendente |
| 04 — Executor compartilhado | Modelos compartilhados por configuração compatível, lotes e filas limitadas | Memória, latência, justiça entre câmeras, expiração e falhas validadas sob carga | Pendente |
| 05 — Pacotes e prontidão da mãe | Schema executável, molde declarativo, comando validador, instalação/ativação/retorno de versão e receitas | Pediatria e segundo pacote de conformidade funcionam sem caminhos especiais; pacote compatível entra sem editar o host | Em andamento parcial: schema, validador estático e molde ([05a](etapas_motor/05a_schema_validador.md)) ciclo local de instalação, ativação e retorno de versão ([05b](etapas_motor/05b_instalacao_local.md)) e administração pelo painel ([05c](etapas_motor/05c_painel_especialistas.md)) e executor local mínimo com segundo pacote de conformidade ([05d](etapas_motor/05d_executor_local.md)) entregues; lote de interface do dashboard, receitas e sombra por câmera em andamento ([05e](etapas_motor/05e_dashboard_sistema_mae.md)); receitas e migração da pediatria pendentes |
| 06 — Workspace de especialistas | Projeto separado com contrato, molde e validação utilizáveis | Pacote produzido fora do repositório é validado e instalado no sistema-mãe | Pendente: itens e **kit mínimo proposto** registrados em [06](etapas_motor/06_workspace_especialistas.md); nada implementado |
| 07 — Fusão térmica | Calibração, associação de fontes e atributos relativos | Erro geométrico, sincronismo e perda de fonte exercitados com dados reais | Pendente |
| 08 — Nós de execução | Um nó inicialmente, transporte, capacidades e métricas | Ganho sobre o local demonstrado; queda, duplicação, expiração e incompatibilidade tratadas | Pendente |

As etapas 01 e 02 são frentes independentes. A referência não bloqueia escrever os contratos;
ela é necessária para avaliar a migração. A operação remota vem depois da validação local.
A posição da fusão térmica pode ser revista explicitamente conforme disponibilidade das fontes.
O schema, molde e validador podem ser implementados antes da etapa 05, quando necessários;
essa etapa marca sua entrega integrada ao ciclo de instalação.
Essa antecipação ocorreu em 04/10/2026 (lote 05a). A etapa 05 só será concluída com o ciclo
de instalação integrado e os demais critérios da tabela.

O trabalho já ultrapassou a fase documental: validador, instalação, painel, executor local e integração
em sombra foram implementados parcialmente, com evolução registrada em 03, 03b e 05e.
O [retorno do motor mínimo](retorno_claude_motor_minimo.md) registra um lote anterior. A prioridade
atual é validar o lote **05f**, implementado em sombra e registrado em
[`05f_configuracao_camera.md`](etapas_motor/05f_configuracao_camera.md), com pendências de integração real.
Os números de 4 câmeras, idade de observação ~1 s e alerta
~2 s continuam metas de ensaio com condições de medição a fechar, conforme a transição.

### Prioridade ajustada após `d16dab6`

A etapa 02 mede o caminho pediátrico atual; ela não demonstra sozinha integração de pacotes com câmeras.
A conexão genérica em sombra e a Cena foram acrescentadas nos lotes 03/03b, mantendo o caminho
pediátrico operacional durante a transição. Pode-se evoluir e testar a integração enquanto a referência
humana é finalizada. Aprovar equivalência da pediatria ou substituir o caminho operacional continua dependendo
da comparação de qualidade. A coleta de negativo longo não bloqueia construir os contratos e o fluxo genérico.

### 05f — Configuração simples por câmera e validação em dois níveis

**Decisão consolidada em 05/10/2026. Estado: implementado em sombra, validação em andamento.** Sublote da etapa 05,
apoiado no motor/Cena de 03/03b e no dashboard 05e. Esta seção preserva o planejamento;
o resultado e os limites estão em `docs/etapas_motor/05f_configuracao_camera.md`.

**Objetivo:** permitir configurar e testar cada câmera pelo fluxo normal do painel, com o caminho
**mestre → recortes → um especialista → uma receita**, sem exigir combinações arbitrárias na primeira versão.
O núcleo continua genérico; a limitação é do modo de operação e deve ser validada no servidor, inclusive
em importações/API, não apenas escondida no formulário. Não criar condicionais por nome de câmera,
especialista ou receita no host.

#### Escopo e configurações

| Item | Regra inicial |
|---|---|
| Mestre | Detecção e rastreamento de `person`, sem exigir classificação criança/adulto para produzir Cena e crops. Outras classes só serão liberadas com compatibilidade e validação explícitas. |
| Especialista | No máximo um especialista selecionado por câmera neste modo, compatível com a classe mestre e fonte. O mesmo pacote instalado pode atender várias câmeras. |
| Receita | Uma versão de receita por câmera, usando somente o especialista selecionado. Pode consumir vários atributos dele e relacionar várias trilhas; um especialista não significa uma pessoa por vez. |
| Diagnóstico da mãe | Permitir câmera somente com mestre/rastreamento/recortes, sem especialista nem receita de análise ativos. Não gerar veredito especializado fictício. |
| ROI | Região de análise da câmera, distinta da margem do crop. Formalizar seleção de detecções na borda, referência de coordenadas e preservação de contexto antes de implementar; a bbox pública continua referida ao quadro original. |
| Crop | Margem horizontal, margem vertical e mínimo em pixels; manter margem única como atalho compatível. Especificar precedência sem somar margens duas vezes. Ajustar aos limites do quadro e preservar a transformação. |
| Execução | Cadência, orçamento desde a recepção e validade dentro dos limites do pacote/receita. A câmera não pode ampliar validade além do contrato nem forçar formato não suportado. |
| Padrões | Perfil compatível sugere padrões; a câmera guarda seus ajustes e a configuração efetiva versionada/identificável. Definir onde o perfil é persistido e sua precedência na implementação; não inventar campos no manifesto sem versionar o contrato. |

O perfil pediátrico pode reproduzir as margens legadas (12% horizontal, 8% vertical, mínimo de 8 px),
depois de conferir a fórmula no código. Esses valores não são padrão universal para todo especialista.
O tamanho mínimo do alvo exigido pelo pacote é diferente do mínimo de margem do crop.

#### Travas e compatibilidade

- Recusar mistura de especialistas na mesma câmera no modo simples; não impedir múltiplos atributos/classes
  de saída do único pacote quando o contrato e a receita os suportarem.
- Mostrar receitas compatíveis e revalidar ao salvar, associar e trocar a versão ativa do pacote.
  Mudança incompatível deve ser explícita e não produzir um falso resultado negativo.
- Preservar receitas existentes com múltiplos especialistas e seus dados. Identificá-las como fora do modo
  simples; não converter, apagar ou alterar silenciosamente sua execução. Definir uma transição explícita
  para câmeras já associadas antes de habilitar o modo simples nelas.
- Aplicar configurações atomicamente: falha mantém a anterior; sucesso invalida atributos e estado temporal
  afetados e impede publicação tardia da configuração anterior. Ajustar ROI/crop não deve reiniciar a análise
  operacional sem necessidade nem misturar configurações no mesmo diagnóstico.
- Mutação administrativa com autorização no servidor, CSRF e auditoria; uso normal pelo formulário, sem
  exigir Python ou edição direta do banco. Cadastro de câmera segue o fluxo padrão existente.
- Manter execução das novas receitas em sombra. Ativação de pacote, habilitação de sombra e ativação
  operacional de alertas continuam sendo operações distintas.

#### Sequência de implementação e evidências

1. **Consolidar contratos mestre/crop:** conferir Cena, Observação, Recorte e Pedido contra o manual;
   documentar quais metadados ficam na mãe e quais chegam ao especialista, identidade de trilha,
   transformação geométrica, ROI, margens, formato e prazo. Não presumir que o envelope documental
   completo já é transmitido pelo executor local.
2. **Persistir configuração simples por câmera:** defaults/perfil, ajustes, versão/hash e validações de
   compatibilidade. Reutilizar serviços de receitas, especialistas e câmera, com migração se necessária.
3. **Adaptar o painel:** seleção de especialista/receita compatível, ROI/crop e parâmetros permitidos;
   diagnóstico mestre sem especialista, estados claros e indicação de configuração incompatível.
4. **Validar nível 1 — mãe/mestre/crop:** go2rtc com vídeos em loop cadastrados pelo painel; pessoas,
   cruzamentos, oclusões, cena vazia, bordas do ROI e reinício. Conferir bbox/trilhas, crop correto por
   sujeito, margens e coordenadas, sem carregar o especialista de papel como requisito do mestre.
5. **Validar nível 2 — especialista/receita:** primeiro pacote determinístico para conferir identidade,
   despacho e validade; depois pacote/modelo real compatível com trechos positivos, negativos e ambíguos.
   Demonstrar duas câmeras com especialistas diferentes e duas com o mesmo pacote e ajustes distintos,
   sem vazamento de atributos ou configuração. Compartilhar pacote não prova execução compartilhada
   eficiente: isso continua sendo etapa 04.
6. **Registrar resultado:** separar teste de interface, contrato, inferência real e qualidade. Falta de
   go2rtc ou fonte deve constar como pendência, não teste aprovado. A virada do loop é descontinuidade
   controlada; repetição não aumenta diversidade nem exposição independente de avaliação.

**Situação em 05/10/2026 — critérios atendidos** (matriz e evidências no [registro 05f](etapas_motor/05f_configuracao_camera.md#fechamento-do-05f-pelo-painel-toolsvalidar_painel_05fpy)): configuração operada **pelo navegador**, IA e sombra subidas pelos laços de produção do painel sobre o go2rtc da mãe, três câmeras de configuração própria (A e B com o pacote real, C com o determinístico de conformidade), crops exibidos e conferidos contra a fórmula do contrato, isolamento ao salvar uma câmera, restrições no backend, receitas anteriores preservadas e CPU explícita honrada (defeito do contrato de dispositivo corrigido neste lote). O 05f fica pronto para revisão, **sem commit e sem arquivamento**. Isto **não** conclui a etapa 05, nem 02, nem 03. Fora do 05f e em aberto: calibração de cadência/capacidade (a meta de 5 %/500 ms por câmera é **hipótese proposta, não aceite aprovado**), qualidade/equivalência da migração pediátrica e o fluxo de especialista externo ([etapa 06](etapas_motor/06_workspace_especialistas.md)). Segundo modelo real não pediátrico **não** é bloqueio do 05f.

**Critérios para concluir 05f:** configuração básica inteiramente operável pelo painel; restrições
aplicadas também no backend; receitas anteriores preservadas; teste do mestre/crop independente do
especialista; execução de especialista real pelo caminho normal; isolamento entre câmeras e configurações;
ausência de eventos antigos após alteração; vídeo/overlay/crops conferidos em navegador e evidências
registradas sem versionar imagens de pessoas. Isso não conclui automaticamente a migração pediátrica,
a etapa 02 ou a etapa 05 inteira.

**Fora deste lote:** composição livre de especialistas, várias receitas simultâneas por câmera, novas
classes sem validação, nós remotos, fusão térmica, treinamento e otimização do executor compartilhado.
A limitação poderá ser ampliada depois de medir necessidade e custo; não remover a extensibilidade do núcleo.

## Como conduzir uma etapa

Ao iniciar uma etapa, criar seu registro em `docs/etapas_motor/NN_nome.md`. Registrar objetivo,
escopo, entregas, dependências, critérios de conclusão e evidências. Criar apenas os registros
das etapas efetivamente iniciadas; a tabela acima basta para as futuras.

O registro da etapa deve deixar claro quais arquivos serão editados e quem está trabalhando
neles quando houver mais de um assistente. Para a etapa 01, o trabalho técnico deve se concentrar
nos dois documentos novos, evitando edição concorrente dos três documentos de arquitetura.

Estados: **pendente → em andamento → concluída e arquivada**. Quando houver bloqueio, registrar
motivo e dependência sem marcar a etapa como concluída. Mudança de escopo exige atualizar o plano
e o registro da etapa; não esconder trabalho faltante no histórico.

## Encerramento e histórico

Ao concluir cada etapa:

1. Conferir seus critérios e preencher o resultado: entregas, validações, limitações e pendências
   transferidas explicitamente para outra etapa. Incluir commits quando existirem.
2. Marcar o registro como **concluído**, com data, e movê-lo para
   `docs/historico/motor_plugins/NN_nome.md` (criar a pasta no primeiro arquivamento).
3. Atualizar esta tabela para **concluída e arquivada**, apontando para o registro no histórico.
   Corrigir links relativos afetados pela mudança. Não manter uma cópia ativa do mesmo registro.

O histórico guarda registros encerrados. Manual do especialista, protocolo dos nós, schemas,
exemplos e validador permanecem em seus locais de uso, com versões atualizadas: são contratos e
ferramentas necessários ao desenvolvimento separado.

Quando todas as etapas do escopo estiverem concluídas, registrar o resultado global e arquivar
este plano em `docs/historico/motor_plugins/plano_motor_plugins.md`, corrigindo os links.
Remover sua cópia ativa e atualizar eventuais referências. Trabalho novo abre outro plano;
este histórico não volta a funcionar como lista de tarefas.

Nenhum arquivo foi arquivado agora: a transição ainda está em andamento. Commit e eventual
integração da branch devem conter somente arquivos revisados e pertinentes ao trabalho.
