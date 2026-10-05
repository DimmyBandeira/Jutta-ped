# Revisão do dashboard 05e

Data: 2026-10-05. Working tree após `d16dab6`, sem commit. Revisão do Codex; implementação não alterada.

## Parecer e evidências

A adaptação do mockup existe no painel ativo e está alinhada à ideia de sistema-mãe: receitas versionadas, associação a câmera, administração de especialistas e diagnóstico de sombra separados dos alertas operacionais. Não é apenas um HTML demonstrativo.

Inspecionei código, templates e capturas existentes `09_monitoramento_completo.png` e `20_mobile_monitoramento.png` em `%TEMP%/jutta_dashboard_shots`. Essas imagens mostram menu lateral/compacto, cartões, gráfico, câmeras, alertas e receitas. Não abri uma nova sessão Edge; não validei vídeo real/overlay. Na imagem móvel, vários popups ocupam grande parte da tela: conferir usabilidade com múltiplos alertas na revisão com vídeo, além de ausência de rolagem horizontal.

Reexecutei receitas, sombra, dashboard, templates, despacho e migrações: **126 testes passaram**, com um aviso de depreciação Starlette/httpx. Não reexecutei os 837 testes nem a fumaça real. Reproduzi separadamente os dois defeitos de concorrência abaixo com o ambiente real de teste do painel, banco isolado e backend falso; nenhuma câmera operacional foi usada.

## Correções antes de encerrar o lote

### 1. P1 — reconciliação antiga religa sombra que o administrador desligou

Em `src/jutta_ped/painel/sombra_service.py`, `reconcile` lê `sombra_ativa` e a versão no banco antes de chamar `_reconciliar_camera`; esta só captura `s.decisao` depois. Se o desligamento ocorrer entre esses pontos, a decisão capturada já é a nova, mas os dados usados para montar são antigos.

Reprodução determinística: interromper entre leitura do snapshot e chamada de `_reconciliar_camera`; fazer POST `acao=desligar`; retomar a reconciliação. Após o POST: banco `False`, porta sem alvo. Após a reconciliação antiga: banco continua `False`, mas gerente informa `ativa` e porta recebe um novo despacho. Pode executar até o próximo ciclo, embora a interface baseada no banco diga desligada.

Correção: vincular cada snapshot a uma revisão/geração de configuração e impedir publicação de montagem baseada em estado superado. Cobrir desligamento **antes** de `_reconciliar_camera`, durante carga, troca de receita e remoção de câmera. A última decisão válida precisa prevalecer; apenas reler o contador depois do snapshot não basta.

### 2. P2 — resultado aposentado reaparece no feed depois de desligar

`_desmontar` espera no máximo 0,3 s pelo despacho e limpa o buffer. O callback `destino` criado por `_montar` continua apontando para `s.buffer`, sem verificar se ainda pertence ao despacho/configuração vigente.

Reprodução: bloquear um `motor.processar` já iniciado; desligar pela rota; confirmar feed vazio; liberar o trabalho antigo. O gerente permanece `desligada`, mas o feed passa de zero para um evento. Ao reconfigurar, o mesmo mecanismo permite publicação tardia da receita antiga entre eventos da nova. Não contamina alertas operacionais, mas torna o diagnóstico incoerente.

Correção: cercar a publicação com identidade de montagem/configuração/geração, rejeitando callbacks aposentados ou classificando-os explicitamente em um histórico separado. Um processamento em curso pode terminar sem precisar matar a thread, mas não deve voltar ao feed ativo como atual. Cobrir desligar, reconfigurar e reinício de sessão com trabalho em andamento.

### 3. P2 — indicador anuncia vídeo disponível com base apenas na configuração

`src/jutta_ped/painel/routes_dashboard.py`, `_stream_disponivel`, conta nome do stream e `go2rtc_sync == "ok"`; para stream não gerenciado basta o nome. A sincronização pode manter `ok` mesmo com a API go2rtc indisponível, quando o hash da configuração não mudou (`go2rtc.py`, `_reconcile`). Ela não comprova produtor conectado ou recebimento recente de quadros.

Logo, o cartão “Câmeras com stream disponível” e a nota “Vídeo disponível no go2rtc” afirmam mais do que a fonte mede. Isso também precisa ser conferido nos selos dos cards. Corrigir nome/descrição para configuração sincronizada, ou usar telemetria real de vídeo com estados indisponível/desconhecido e validade da sondagem. Não sondar de forma bloqueante dentro de cada renderização nem confundir cadastro do stream com conexão à câmera.

## Próxima etapa

Corrigir os três pontos com regressões e conferir o painel com vídeo/overlay go2rtc. A revisão visual com vídeo pode seguir como pendência explícita se a fonte não estiver disponível, mas não declarar o lote totalmente validado ao vivo.

Depois seguir para o lote de arquitetura já acordado: mestre/trilhas expostos como Cena genérica, contexto espacial e temporal nas regras da mãe, política de alerta ativo diante de indeterminado e adaptação do classificador atual para especialista de papel. Preservar integração do dashboard e contratos existentes. Não iniciar nós, treinamento ou repetir bateria de capacidade. Etapas 02, 03 e 05 continuam abertas.

## Prompt curto ao Claude

```text
Leia docs/revisao_dashboard_05e.md. Corrija os três achados antes de
encerrar o dashboard, reproduzindo as duas corridas com barreiras/eventos
determinísticos. Valide permissões e ciclo de vida sem reiniciar a IA.
Corrija a semântica de disponibilidade do vídeo e faça a revisão com
go2rtc/overlay real se houver fonte disponível; registre o que não rodou.
Preserve working tree, pesos e documentos anteriores. Não repita a bateria.
Entregue evidências curtas e deixe pronto para revisão, sem commit.
Após isso, o próximo lote é mestre/Cena genéricos e contexto entre trilhas.
```
