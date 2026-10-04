# Prompt para discussão de arquitetura com outro modelo

Cole o bloco abaixo numa conversa nova e anexe os arquivos da lista "Anexar". Não peça código: o objetivo é
revisar e melhorar o desenho antes da implementação.

## Anexar (nesta ordem de importância)

| Arquivo | Para quê |
|---|---|
| `docs/motor_de_analise.md` | Desenho do motor, plugins, nós de GPU, estado e ordem de execução (documento principal) |
| `docs/transicao_motor_de_analise.md` | Revisão consolidada, limites das evidências, compatibilidade e critérios para avançar |
| `docs/painel_web.md` | Painel atual: arquitetura, login/papéis, go2rtc, SSE/popups, banco, testes |
| `docs/pediatria_service_api.md` | Serviço de IA e API HTTP atual (sessões, eventos, evidências) |
| `plugin/README.md` | Contrato de plugin já existente entre a IA e a plataforma mãe |
| `plugin/schemas/overlay.schema.json`, `event.schema.json`, `config.schema.json`, `ui.schema.json` | Formatos de overlay, evento, configuração e UI |
| `docs/testes_operacionais/2026-10-03_1352.md` | Medições reais (tempos, fps, defasagem, CPU) que embasam as decisões |
| `docs/pediatria_videos_reais_2026-06-19.md` | Resultados do detector em vídeos reais e limitações do modelo |
| `README.md` | Visão geral do projeto |

Opcionais, só se o outro modelo pedir: `src/jutta_ped/service/runtime.py` (sessão, captura, laço de
análise), `src/jutta_ped/service/placement.py` (política de dispositivo), `modulo/pediatria/crop_pipeline.py`
(mestre → especialista por recorte), `src/jutta_ped/painel/models.py` e `migracoes/` (banco).

## Prompt

```
Você é um arquiteto de software sênior em visão computacional e sistemas distribuídos. Vou discutir com você
o desenho de um "motor de análise" antes de qualquer implementação. NÃO escreva código de produção; quero
crítica, alternativas, riscos e perguntas. Responda em português, direto, com recomendação clara.

CONTEXTO
Projeto "Jutta-ped": detecção de criança desacompanhada em câmeras de hospital (YOLO/OpenVINO, rastreamento
ByteTrack, analisador de companhia, serviço FastAPI, painel web FastAPI+Jinja2+HTMX, vídeo via go2rtc).
Roda em Windows hoje (Linux depois). Máquina de referência: i9-11900H (16 threads), Intel UHD, RTX 3050 4 GB.
Os documentos anexos descrevem o estado atual; o principal é docs/motor_de_analise.md.

OBJETIVO CONFIRMADO
Preparar este projeto como sistema-mãe do motor e mover a pediatria para o desenho novo:
classificação como especialista, companhia/risco como receita e regras, infraestrutura no host.
Depois, desenvolver especialistas em projeto/workspace separado no VS Code, produzindo pacotes
instaláveis conforme o contrato do host. Pacotes que usem capacidades já suportadas devem entrar
sem alteração do código do sistema-mãe. Sistema-mãe aqui é o host standalone deste projeto;
a integração com o WebGuardião Core é outra fronteira.
O foco é plugins e nós. A pediatria é o primeiro caso real e a régua da migração. Desenho e medição
são frentes independentes: não espere a bateria para especificar os contratos. O marco de host
pronto inclui a pediatria migrada e um segundo pacote de conformidade, sem finalidade operacional,
para provar a extensão. Nós remotos são desenhados agora e implementados após validar o local.

EVIDÊNCIAS E LIMITES (nesta máquina)
- Relatório operacional de 03/10: uma câmera a 14,7 fps, ~64% de CPU total, ~500 MB de RAM por câmera
  e 0 MB de VRAM, com OpenVINO e vídeos de demonstração em loop. Há observações de CPU até ~70%.
- Medições exploratórias abaixo vieram de scripts em pasta temporária, fora do repositório; não
  constam desse relatório. Precisam de bateria reproduzível em tools/ antes de dimensionar o executor:
- Pessoa yolo11n 320 px: 5,3 ms CPU. Especialista criança/adulto 640 px: 19-29 ms CPU, 16 ms iGPU,
  13-16 ms PyTorch CUDA fp16 (lote 1), ~5 ms/recorte em lote 8.
- 8 threads, cada uma com seus modelos na GPU: ~41 fps no total, CPU 21%, 371 MB de VRAM.
  GIL como gargalo é HIPÓTESE; perfil real ainda pendente.
- Os modelos padrão são OpenVINO e rodam na CPU, mesmo havendo CUDA.
- Câmeras térmicas (bi-espectrais) têm dois streams com campo de visão, resolução, fps e paleta diferentes.

DECISÕES JÁ TOMADAS (não reabrir sem boa razão)
1. Só o detector mestre (pessoa) desenha caixa, rotulada "analisando", com cor indo ao vermelho conforme a
   pontuação se aproxima do limiar das regras; vermelho = alerta.
2. Cascata: mestre (~5 fps, piso 3, CPU) → extratores sobre recortes → regras com condições obrigatórias +
   evidências ponderadas → alerta com severidade. Pediatria vira conjunto de regras do sistema.
3. "Receitas" (extratores + regras + parâmetros) habilitadas por câmera, com ajustes por câmera.
4. Parâmetros de tempo em segundos (convertidos pela taxa de análise), não em frames.
5. Calor térmico: só cores (índice relativo), sem temperatura absoluta por enquanto; exige calibração
   por câmera entre os dois streams.
6. Banco com migrações (Alembic); esquema de alerta genérico (plugin, tipo, severidade, status, receita).
7. Fundamentos já implementados: política de dispositivo honesta (OpenVINO=CPU, CUDA troca por .pt irmão com
   reserva em CPU), analysis_fps, migrações.
8. Três contratos separados: integração com o Core, especialista interno e nó executor.
9. Prazo por observação desde a captura, com idade máxima por atributo e receita. Fila, lote,
   transmissão e inferência gastam o mesmo orçamento; retentativas não o renovam.
10. Pedido lógico separado de tentativa: trabalho duplicado é permitido, aceitação de no máximo um
    resultado válido por pedido; resultado antigo não substitui atributo mais novo.
11. Cache expirado ou resultado ausente vira indeterminado, nunca ausência automática de risco.
12. Primeira versão com pacotes declarativos só da equipe. Lógica própria fica no repositório;
    sem upload de Python de terceiros. Validação de pacote e pesos continua necessária.
13. Referência medida e modo sombra antes do executor compartilhado; transbordo remoto por último.
    O modo sombra compara alertas perdidos, falsos e repetidos por troca de trilha, além de desempenho.
14. Consumidores atuais devem continuar funcionando: schemas fixam ia.pediatria e rótulos pediátricos;
    escolher nova versão e/ou adaptador para a caixa genérica e os demais contratos.

REFERÊNCIA PROPOSTA PARA ENSAIO (não são garantias demonstradas)
- Standalone primeiro, contratos preparados para o Core; 4 câmeras, uma receita pediátrica.
- Criança sozinha: observação com idade máxima ~1 s e alerta ~2 s. Fechar marcos de medição,
  persistência e percentil; regras com persistência maior precisam de meta própria.
- Candidatos de vídeo: os dois DVR descritos no relatório de 19/06, sujeitos à revisão e seleção final.
  Incluir casos negativos e definir pessoas simultâneas. Duplicar streams mede carga, não diversidade.

PROPOSTAS EM DISCUSSÃO (seções 8.1 e 8.2 do documento)
A. Plugins de especialista instaláveis: pacote .zip com manifest.json (atributos de saída tipados,
   modelo, custo, parâmetros, requisitos, versão de protocolo), declarativos da equipe na primeira versão;
   executor central que processa em lote recortes de todas as câmeras; plugin só produz atributos.
B. Nós de GPU de transbordo em outros servidores da mesma VLAN privada e offline: local primeiro; só
   transborda se houver benefício medido e prazo restante suficiente. WebSocket binário e JPEG são
   candidatos, ainda a comparar. Se o nó cai, tentativa local dentro do mesmo orçamento; cache só enquanto
   válido para atributo e receita, depois indeterminado. Disjuntor por nó; métricas de tráfego,
   latência, timeouts e descartes na página "Servidor" do admin.
   Os antigos limites independentes de 150/300/500 ms foram retirados.

O QUE QUERO DE VOCÊ
1. Critique A e B: furos, riscos, casos de borda, o que está sobre-engenhado para 2-3 nós.
2. Contrato do manifesto e do protocolo: o que falta, o que sobra, como versionar e evoluir sem quebrar.
3. Transporte (WebSocket vs gRPC vs fila), formato dos recortes (JPEG vs bruto), ordenação, retentativas
   e idempotência, backpressure e política de descarte.
4. Validação de pacotes declarativos e pesos da equipe; isolamento de falhas e recursos dos executores.
   Não projetar instalação de código de terceiros para esta versão.
5. Como medir saúde do enlace e do transbordo e quais limites usar para o selo saudável/atenção/crítico.
6. Fusão térmica: calibração, sincronismo entre streams e onde ela vive (plugin ou núcleo).
7. Critérios de aceitação em etapas pequenas e testáveis, respeitando referência → sombra → executor
   compartilhado e remoto por último; como medir qualidade dos alertas e validar as metas de tempo.
8. As perguntas que você faria a mim antes de aprovar o desenho.
9. O que o sistema-mãe precisa entregar para permitir o desenvolvimento separado de especialistas:
   manual, exemplos, verificações de conformidade e ciclo do pacote. Confira a decomposição da
   pediatria e os critérios de prontidão da seção 1 de docs/transicao_motor_de_analise.md.

Termine com: (a) as 5 maiores decisões abertas, cada uma com sua recomendação; (b) o que você mudaria no
documento anexo. Não repita o que já está decidido.
```
